"""
Email sending via Resend, not Azure Communication Services -- ACS was blocked on the
Microsoft.Communication resource provider needing an admin registration that never went
through (see infra/modules/comms). Resend is a third-party HTTP API, so there is nothing
to provision, just an API key.

Used by two callers today: the daily certificate-expiry-reminder timer function
(send_expiry_reminders) and confirm_document's "new training assigned" notification,
both in function_app.py. RESEND_API_KEY/RESEND_FROM_ADDRESS are read from the
environment the same way SQL_PASSWORD already is -- a Key Vault reference resolved by
the Function App's managed identity, never a literal value in code or in Terraform.
"""

from __future__ import annotations

import os


class CommsNotConfigured(RuntimeError):
    """RESEND_API_KEY is unset. Distinct from a send failure: this means nobody has
    configured Resend yet, not that Resend rejected the request."""


def _send(to_email: str, subject: str, html: str, company_name: str) -> None:
    """
    Shared plumbing for both email types below. Raises on failure -- every caller here
    is responsible for catching per-recipient, so one bad address never stops a batch.
    """
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        # Fail loud and specific, the same reason shared/auth.py refuses to sign with a
        # missing JWT_SIGNING_SECRET rather than silently no-op -- a job that quietly
        # sends nothing looks identical to one that ran and found nobody due.
        raise CommsNotConfigured(
            "RESEND_API_KEY is not set -- Resend was never configured "
            "(infra/modules/comms, resend_api_key)."
        )
    from_address = os.getenv("RESEND_FROM_ADDRESS", "onboarding@resend.dev")

    import resend

    resend.api_key = api_key
    resend.Emails.send({
        "from": "{} Training <{}>".format(company_name, from_address),
        "to": [to_email],
        "subject": subject,
        "html": html,
    })


def send_expiry_email(
    to_email: str,
    employee_name: str,
    doc_title: str,
    expires_at,
    company_name: str,
) -> None:
    """One reminder email for a certificate about to expire."""
    expiry_str = (
        expires_at.strftime("%B %d, %Y")
        if hasattr(expires_at, "strftime")
        else str(expires_at)[:10]
    )
    _send(
        to_email,
        subject="Action needed: {} expires soon".format(doc_title),
        html=(
            "<p>Hi {},</p>"
            "<p>Your certificate for <strong>{}</strong> is set to expire on "
            "<strong>{}</strong>.</p>"
            "<p>Sign in to the training portal to retake it before then.</p>"
        ).format(employee_name, doc_title, expiry_str),
        company_name=company_name,
    )


def send_manager_reminder_email(
    to_email: str,
    employee_name: str,
    missing_titles: list,
    expired_titles: list,
    company_name: str,
) -> None:
    """
    One on-demand nudge, sent when a manager clicks "Send reminder" on My Team.

    Distinct from send_expiry_email (sent automatically, one per about-to-lapse
    certificate, by the daily timer): this is manager-triggered and lists everything
    currently outstanding -- never completed, and lapsed -- in one email, computed by
    the caller (function_app.get_team_completion's sibling route) from that person's
    real standing, never invented here.
    """
    items = "".join("<li>{} -- not yet completed</li>".format(t) for t in missing_titles)
    items += "".join("<li>{} -- expired, needs retaking</li>".format(t) for t in expired_titles)
    _send(
        to_email,
        subject="Reminder: required training outstanding",
        html=(
            "<p>Hi {},</p>"
            "<p>Your manager sent a reminder about training still outstanding:</p>"
            "<ul>{}</ul>"
            "<p>Sign in to the training portal to catch up.</p>"
        ).format(employee_name, items),
        company_name=company_name,
    )


def send_new_training_email(
    to_email: str,
    employee_name: str,
    doc_title: str,
    company_name: str,
) -> None:
    """
    One notification email, sent when a document is newly required for a role
    (confirm_document, makeRequired=true, and only on the pair's FIRST assignment --
    see SqlBank.add_role_requirement's return value). Re-confirming an already-required
    document must not resend this.
    """
    _send(
        to_email,
        subject="New training assigned: {}".format(doc_title),
        html=(
            "<p>Hi {},</p>"
            "<p><strong>{}</strong> has just been assigned to your role.</p>"
            "<p>Sign in to the training portal to see it on your dashboard.</p>"
        ).format(employee_name, doc_title),
        company_name=company_name,
    )
