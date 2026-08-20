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

DECLARE @company_id INT =
    (SELECT id FROM Companies WHERE name = 'Quadrant Technologies');

IF @company_id IS NULL
BEGIN
    RAISERROR('Companies is empty - run org_seed.sql before role_codes.sql.', 16, 1);
    RETURN;
END

UPDATE r
SET role_code = v.role_code
FROM Roles r
JOIN (VALUES
    -- Software Engineering
    ('SDE 1',                              'SDE1'),
    ('SDE 2',                              'SDE2'),
    ('SDE 3',                              'SDE3'),
    ('Engineering Intern',                 'INTERN'),
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
    ('Sales Specialist',                   'SALES_OPS'),
    -- Security: one practice, one body of material, same reasoning as DevOps above. This
    -- code is new (added to SEED_ROLES in src/quizgen/rolemap.py) rather than borrowed
    -- from engineering -- a Security Analyst pointed at SDE2 would be served software
    -- engineering material, which is the failure 016 warned about.
    ('Director of Cybersecurity',          'SECURITY'),
    ('Senior Security Architect',          'SECURITY'),
    ('Information Security Engineer',      'SECURITY'),
    ('Security Analyst',                   'SECURITY'),
    -- Leadership, mapped onto existing codes rather than new ones. Both are judgment
    -- calls and both are safe to change: role_scope is matched as IN ('ALL', <code>), so
    -- a code with nothing scoped to it yet simply serves company-wide training, exactly
    -- as an unmapped role does today. Nothing is lost by being wrong here, which is why
    -- these are filled in rather than left for a meeting.
    --   CTO sits above Software Development, so it takes the same organisational
    --   strategy and enterprise risk material as the engineering director.
    ('Chief Technology Officer',           'SWE_DIRECTOR'),
    --   VP of Sales is revenue leadership; VP_REVENUE_OPS already existed and was unused.
    ('VP of Sales',                        'VP_REVENUE_OPS')
) AS v(title, role_code) ON v.title = r.title
WHERE r.role_code IS NULL;

DECLARE @mapped INT = @@ROWCOUNT;

-- QuizgenRoles powers the upload-target dropdown. seed_roles() intentionally does
-- nothing once that catalog is non-empty, so a live database would never discover a
-- newly added org role from Python's default list alone. MERGE this one role here,
-- after org_seed has created it, without changing any manager-edited catalog entries.
MERGE dbo.QuizgenRoles AS target
USING (SELECT 'INTERN' AS role_code, @company_id AS company_id) AS source
   ON target.role_code = source.role_code AND target.company_id = source.company_id
WHEN NOT MATCHED THEN INSERT (role_code, company_id, title, description)
    VALUES ('INTERN', @company_id, 'Engineering Intern',
            'Audience onboarding, safe delivery, testing, and engineering fundamentals.');

-- Still NOT mapped, and this is not an oversight:
--
--   Leadership      Sales Lead
--   HR / Finance    HR Lead, Talent Acquisition Manager, Senior Recruiter, Recruiter,
--                   People Operations Coordinator, Senior Accountant, Accountant,
--                   Accounting Coordinator, Senior Financial Analyst, Financial Analyst
--   IT Support      IT Support Lead, IT Support Manager, Senior IT Support Specialist,
--                   IT Support Specialist, Helpdesk Technician, End User Support,
--                   Senior IT Engineer, Senior Systems Administrator, System Administrator
--
-- quizgen's SEED_ROLES has no code for any of these. Unlike the security roles above,
-- NOBODY CURRENTLY HOLDS THEM -- every one of the 18 real employees is in engineering,
-- DevOps, security, sales or leadership -- so mapping them would be inventing training
-- paths for jobs the company has not filled. They cost nothing while empty and serve
-- company-wide training the moment someone is hired into one.
--
-- Closing this later means adding codes to SEED_ROLES in src/quizgen/rolemap.py and a
-- line above. Safe to do at any time: role_scope is matched as IN ('ALL', <code>), so a
-- new code never removes company-wide material, it only makes role-specific material
-- targetable. The thing to avoid is pointing one of these at an existing unrelated code
-- -- that serves one role's material to another, which is the failure 016 warned about.

DECLARE @unmapped INT =
    (SELECT COUNT(*) FROM Roles WHERE role_code IS NULL);

PRINT 'role_codes: mapped ' + CAST(@mapped AS VARCHAR) + ' role(s) this run; '
    + CAST(@unmapped AS VARCHAR) + ' still unmapped (they serve company-wide training).';
GO
