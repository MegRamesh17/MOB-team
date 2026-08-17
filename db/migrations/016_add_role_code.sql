-- 016_add_role_code.sql
--
-- Links the org chart's Roles to quizgen's training role codes.
--
-- Numbered 016 to clear everything already taken or claimed: 012 (add_auth, merged),
-- 013 (generation_cycles), 014 (certificates, on add-certificates), 015 (trusted_links).
--
-- WHY THIS IS NEEDED
-- Two role registries exist and nothing joins them:
--
--   Roles (006)            title, level, access_role   -- who you are, what you may do
--   quizgen SEED_ROLES     role_code, title            -- what you are taught
--
-- Their titles do not match. The org chart says 'SDE 2'; quizgen says 'Software
-- Development Engineer 2'. Several org roles -- Security Analyst, Accountant, Recruiter --
-- have no quizgen code at all. So a login cannot derive the training role by matching
-- titles, and guessing with a fuzzy match would be worse than not trying: the failure
-- mode is serving one role's material to another.
--
-- WHY NULLABLE
-- An unmapped role gets NULL, and api/shared/auth.py turns NULL into "ALL" -- which
-- serves company-wide training only. A missing mapping therefore UNDER-serves rather than
-- leaking, which is the direction an error should fail in. Filling these in is a
-- deliberate act by someone who knows what a role should be taught, not a default.

ALTER TABLE Roles ADD role_code NVARCHAR(40) NULL;
GO

-- Mappings that are unambiguous. Anything requiring a judgment call is left NULL
-- on purpose -- see the list at the bottom.
UPDATE r
SET role_code = v.role_code
FROM Roles r
JOIN (VALUES
    ('SDE 1',                              'SDE1'),
    ('SDE 2',                              'SDE2'),
    ('SDE 3',                              'SDE3'),
    ('Software Engineering Manager',       'SWE_MANAGER'),
    ('Director of Software Engineering',   'SWE_DIRECTOR'),
    ('Customer Success Manager',           'CSM'),
    ('Director of Client and Revenue Ops', 'CSM_DIRECTOR'),
    ('Customer Success Associate',         'CUSTOMER_SERVICE'),
    ('Sales Specialist',                   'SALES_OPS'),
    ('Senior Account Executive',           'ACCOUNT_TEAM'),
    ('Account Executive',                  'ACCOUNT_TEAM'),
    ('Senior DevOps',                      'CLOUD_DEVOPS'),
    ('DevOps Engineer',                    'CLOUD_DEVOPS'),
    ('Junior DevOps',                      'CLOUD_DEVOPS'),
    ('Director of DevOps',                 'CLOUD_DEVOPS')
) AS v(title, role_code) ON v.title = r.title
WHERE r.role_code IS NULL;
GO

-- Left NULL deliberately, so they serve company-wide training until someone decides:
--
--   Cybersecurity      Director of Cybersecurity, Senior Security Architect,
--                      Information Security Engineer, Security Analyst
--   Human Resources    HR Lead, Talent Acquisition Manager, Senior Recruiter, Recruiter,
--                      People Operations Coordinator
--   Finance            Senior Accountant, Accountant, Accounting Coordinator,
--                      Senior Financial Analyst, Financial Analyst
--   IT Support         IT Support Lead, IT Support Manager, Senior IT Support Specialist,
--                      IT Support Specialist, Helpdesk Technician, End User Support,
--                      Senior IT Engineer, Senior Systems Administrator,
--                      System Administrator
--   Leadership         Chief Technology Officer, Sales Lead, VP of Sales
--
-- quizgen has no code for most of these today. Two ways to close it: add codes to
-- SEED_ROLES in src/quizgen/rolemap.py and map them here, or accept that these roles take
-- company-wide training only. Both are fine; picking silently is not.
