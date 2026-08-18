-- role_codes.sql
-- Links each org-chart role to the training role code that decides what it is taught.
-- Run AFTER org_seed.sql, which creates the Roles this updates.
--
-- WHY THIS IS A SEED FILE AND NOT A MIGRATION
-- 016_add_role_code.sql already contains this mapping, and it has already run. It mapped
-- nothing, because the pipeline runs Migrate before Seed: at the moment 016 executed,
-- org_seed.sql had not created any Roles yet, so its UPDATE ... JOIN matched zero rows.
-- Zero rows updated is not an error, so it reported success and was recorded in
-- SchemaMigrations as applied -- which means it will never run again.
--
-- A new migration would fail exactly the same way for exactly the same reason. The
-- mapping has to happen after the roles exist, so it belongs here.
--
-- WHAT WENT WRONG WITHOUT IT
-- api/shared/auth.py turns a NULL role_code into "ALL". With every role unmapped, every
-- employee resolved to ALL, so role-scoped training served company-wide material to
-- everybody. Worse, get_team excludes ALL from uploadTargets for anyone who is not admin
-- or executive -- so every manager and director had ZERO upload targets and could not
-- push a document to anyone.
--
-- SAFE TO RE-RUN. Only fills NULLs, so a code set deliberately by someone who knows what
-- a role should be taught is never overwritten.
--
-- IF ROLE TITLES CHANGE
-- This matches on r.title exactly. Renaming a role in org_seed.sql silently stops it
-- matching here -- the join finds nothing and the role quietly falls back to ALL rather
-- than erroring. The unmapped count printed at the bottom is what makes that visible, so
-- read it rather than assuming a clean run means a complete one.

SET NOCOUNT ON;

UPDATE r
SET role_code = v.role_code
FROM Roles r
JOIN (VALUES
    -- Software Engineering
    ('SDE 1',                              'SDE1'),
    ('SDE 2',                              'SDE2'),
    ('SDE 3',                              'SDE3'),
    ('Software Engineering Manager',       'SWE_MANAGER'),
    ('Director of Software Engineering',   'SWE_DIRECTOR'),
    -- DevOps: one practice, so every level maps to the same body of material
    ('Senior DevOps',                      'CLOUD_DEVOPS'),
    ('DevOps Engineer',                    'CLOUD_DEVOPS'),
    ('Junior DevOps',                      'CLOUD_DEVOPS'),
    ('Director of DevOps',                 'CLOUD_DEVOPS'),
    -- Sales and customer-facing
    ('Senior Account Executive',           'ACCOUNT_TEAM'),
    ('Account Executive',                  'ACCOUNT_TEAM'),
    ('Customer Success Manager',           'CSM'),
    ('Director of Client and Revenue Ops', 'CSM_DIRECTOR'),
    ('Customer Success Associate',         'CUSTOMER_SERVICE'),
    ('Sales Specialist',                   'SALES_OPS')
) AS v(title, role_code) ON v.title = r.title
WHERE r.role_code IS NULL;

DECLARE @mapped INT = @@ROWCOUNT;

-- Deliberately NOT mapped, and this is not an oversight:
--
--   Cybersecurity   Director of Cybersecurity, Senior Security Architect,
--                   Information Security Engineer, Security Analyst
--   Leadership      Chief Technology Officer, Sales Lead, VP of Sales
--   HR / Finance    HR Lead, Talent Acquisition Manager, Senior Recruiter, Recruiter,
--                   People Operations Coordinator, Senior Accountant, Accountant,
--                   Accounting Coordinator, Senior Financial Analyst, Financial Analyst
--   IT Support      IT Support Lead, IT Support Manager, Senior IT Support Specialist,
--                   IT Support Specialist, Helpdesk Technician, End User Support,
--                   Senior IT Engineer, Senior Systems Administrator, System Administrator
--
-- quizgen's SEED_ROLES has no code for any of these, so there is nothing to map them to.
-- Guessing from the title would be worse than leaving them: an unmapped role falls back
-- to company-wide training, which UNDER-serves visibly, while a wrong guess confidently
-- serves one role's material to another. Closing this means adding codes to SEED_ROLES in
-- src/quizgen/rolemap.py and a line above -- a decision about what a job should be taught,
-- which is not a decision this file should make on its own.

DECLARE @unmapped INT =
    (SELECT COUNT(*) FROM Roles WHERE role_code IS NULL);

PRINT 'role_codes: mapped ' + CAST(@mapped AS VARCHAR) + ' role(s) this run; '
    + CAST(@unmapped AS VARCHAR) + ' still unmapped (they serve company-wide training).';
GO
