"""
Who may publish training to whom.

UPLOAD-02/03: a manager may upload for the roles held by people who report to them. The
upload screen only offers those, but a hidden option is not a permission check — the same
request re-sent with a different role_code in the body has to be refused server-side, and
that is what these cover.

The sharp edge is "ALL". It reads like just another role code and it is not: it means
every employee in the company, including everyone outside your reporting chain. Several
org roles are unmapped in 016_add_role_code.sql and surface as "ALL", so without care a
manager inherits company-wide publishing rights from one report whose role nobody
happened to map yet. That specific path is tested below.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import devauth  # noqa: E402


def ident(employee_id, access_role="manager"):
    person = next(p for p in devauth.DIRECTORY if p["employee_id"] == employee_id)
    return devauth.Identity(
        employee_id=employee_id, email=person["email"], company_id=1,
        access_role=access_role, name=person["name"],
        role_code=person["role_code"], manager_id=person["manager_id"])


DANA, PRIYA, ETHAN = 1, 2, 3


class TestSubtreeDefinesTargets(unittest.TestCase):
    def test_manager_gets_the_roles_their_reports_hold(self):
        # Dana manages Ethan (SDE2), Maya (SDE1) and Liam (Security Analyst → unmapped).
        self.assertEqual(devauth.permitted_upload_roles(ident(DANA)), {"SDE1", "SDE2"})

    def test_a_different_manager_gets_a_different_set(self):
        # Priya's reports are all Account Executives. No overlap with Dana's engineers,
        # which is the isolation the requirement is actually about.
        self.assertEqual(
            devauth.permitted_upload_roles(ident(PRIYA, "director")), {"ACCOUNT_TEAM"})

    def test_someone_elses_role_is_not_permitted(self):
        dana = devauth.permitted_upload_roles(ident(DANA))
        priya = devauth.permitted_upload_roles(ident(PRIYA, "director"))
        self.assertNotIn("ACCOUNT_TEAM", dana)
        self.assertNotIn("SDE2", priya)
        self.assertFalse(dana & priya, "two managers should share no upload targets here")

    def test_an_employee_can_publish_to_nobody(self):
        self.assertEqual(devauth.permitted_upload_roles(ident(ETHAN, "employee")), set())


class TestCompanyWideIsNotAManagerRight(unittest.TestCase):
    """
    ALL means everyone. It is not "a role you control".
    """

    def test_manager_cannot_publish_company_wide(self):
        self.assertNotIn("ALL", devauth.permitted_upload_roles(ident(DANA)))

    def test_director_cannot_either(self):
        self.assertNotIn("ALL", devauth.permitted_upload_roles(ident(PRIYA, "director")))

    def test_admin_and_executive_can(self):
        for tier in ("admin", "executive"):
            with self.subTest(tier=tier):
                self.assertIn("ALL", devauth.permitted_upload_roles(ident(DANA, tier)))

    def test_an_unmapped_report_does_not_grant_company_wide(self):
        # THE trap. Liam Chen is a Security Analyst, which 016_add_role_code.sql leaves
        # NULL, so his role_code is "ALL". Collecting subtree role codes naively hands
        # Dana company-wide publishing rights because of one unmapped report.
        liam = next(p for p in devauth.DIRECTORY if p["employee_id"] == 5)
        self.assertEqual(liam["role_code"], "ALL", "fixture assumption: Liam is unmapped")
        self.assertTrue(any(p["employee_id"] == 5 for p in devauth.reports_of(DANA)))
        self.assertNotIn("ALL", devauth.permitted_upload_roles(ident(DANA)))


class TestNewlyCreatedRoles(unittest.TestCase):
    def test_a_role_created_in_the_same_request_is_allowed(self):
        # The documented flow: the AI flags a role the document names, the manager adds
        # it, and assigns to it. Refusing would break that in the one case it exists for.
        allowed = devauth.permitted_upload_roles(ident(DANA), extra=["PLATFORM_ENG"])
        self.assertIn("PLATFORM_ENG", allowed)

    def test_naming_ALL_as_a_new_role_grants_nothing(self):
        # ALL is discarded AFTER extras are merged, so a manager cannot mint company-wide
        # rights by declaring "ALL" as a role they just created. Ordering, not an
        # explicit check — worth a test precisely because reordering those two lines
        # would silently open it.
        self.assertNotIn("ALL", devauth.permitted_upload_roles(ident(DANA), extra=["ALL"]))


class TestReportingChain(unittest.TestCase):
    """
    Patches devauth.directory rather than DIRECTORY.

    directory() returns the seeded credential file when one exists and falls back to
    DIRECTORY otherwise — so mutating the list does nothing once .auth/users.json is
    present, and a test that did would pass or fail depending on whether someone had run
    seed_demo_users. Patching the function covers both.
    """

    def _patch(self, people):
        original = devauth.directory
        devauth.directory = lambda: people
        self.addCleanup(lambda: setattr(devauth, "directory", original))

    def test_subtree_is_walked_not_just_direct_reports(self):
        # Flat today, so this asserts the mechanism rather than the current org chart:
        # a deeper report must appear, or a director sees almost nobody.
        self._patch(list(devauth.directory()) + [dict(
            employee_id=99, name="Deep Report", email="deep@demo.com",
            title="SDE 1", role_code="SDE3", access_role="employee", manager_id=3)])

        people = devauth.reports_of(DANA)
        self.assertIn(99, [p["employee_id"] for p in people],
                      "someone reporting to Dana's report must be in Dana's subtree")
        self.assertIn("SDE3", devauth.permitted_upload_roles(ident(DANA)))
        self.assertNotIn(99, [p["employee_id"] for p in devauth.reports_of(DANA, direct_only=True)])

    def test_a_cycle_terminates(self):
        # manager_id is a self-referencing FK with nothing stopping a loop. A malformed
        # org chart should return an odd team, not hang the server.
        self._patch(list(devauth.directory()) + [
            dict(employee_id=98, name="Loop A", email="a@demo.com", title="X",
                 role_code="SDE1", access_role="employee", manager_id=97),
            dict(employee_id=97, name="Loop B", email="b@demo.com", title="X",
                 role_code="SDE1", access_role="employee", manager_id=98),
        ])
        # Each is the other's manager. Walking must terminate rather than spin.
        self.assertEqual([p["employee_id"] for p in devauth.reports_of(98)], [97])
        self.assertIsInstance(devauth.reports_of(DANA), list)


if __name__ == "__main__":
    unittest.main()
