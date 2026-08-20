-- org_seed.sql
-- Departments > Teams > Roles for Quadrant Technologies.
-- Run after migrations 006/007/009, before seed_data.sql.
--
-- SAFE TO RE-RUN. Every insert is guarded by WHERE NOT EXISTS, and every foreign key is
-- resolved by name rather than by position. The previous version wrote
-- `INSERT INTO Roles ... VALUES (8, 'SDE 2', ...)` with a comment explaining that 8 meant
-- the eighth row inserted into Teams. That worked exactly once, on an empty database, and
-- silently attached roles to the wrong teams on any other. Since the seed now runs from
-- CI it has to be repeatable, so IDs are looked up, never assumed.
--
-- access_role is about system permissions, not seniority: Coordinators and Analysts are
-- 'employee' however senior they are in practice. `level` carries seniority separately
-- (0 = most senior in the department).

SET NOCOUNT ON;

-- ---------------------------------------------------------------------------
-- Company
-- ---------------------------------------------------------------------------
-- 009_add_multitenancy.sql already inserts Quadrant Technologies, but migrations run once
-- and seeds run repeatedly against databases in unknown states. Guarded so this file
-- stands on its own.
INSERT INTO Companies (name, industry)
SELECT 'Quadrant Technologies', 'Technology'
WHERE NOT EXISTS (SELECT 1 FROM Companies WHERE name = 'Quadrant Technologies');

DECLARE @company_id INT =
    (SELECT id FROM Companies WHERE name = 'Quadrant Technologies');

-- ---------------------------------------------------------------------------
-- Departments
-- ---------------------------------------------------------------------------
INSERT INTO Departments (name, company_id)
SELECT v.name, @company_id
FROM (VALUES
    ('Human Resources'),
    ('Software Development'),
    ('Sales'),
    ('IT Support')
) AS v(name)
WHERE NOT EXISTS (
    SELECT 1 FROM Departments d
    WHERE d.name = v.name AND d.company_id = @company_id
);

-- ---------------------------------------------------------------------------
-- Teams — each department gets a Leadership team plus its named sub-functions
-- ---------------------------------------------------------------------------
INSERT INTO Teams (department_id, name)
SELECT d.id, v.team_name
FROM (VALUES
    ('Human Resources',     'HR Leadership'),
    ('Human Resources',     'Talent Acquisition'),
    ('Human Resources',     'People Operations'),
    ('Human Resources',     'Accounting'),
    ('Human Resources',     'Financial Planning'),
    ('Software Development', 'Engineering Leadership'),
    ('Software Development', 'Cybersecurity'),
    ('Software Development', 'Software Engineering'),
    ('Software Development', 'DevOps'),
    ('Sales',               'Sales Leadership'),
    ('Sales',               'New Business'),
    ('Sales',               'Client and Revenue Ops'),
    ('IT Support',          'IT Leadership'),
    ('IT Support',          'Help Desk'),
    ('IT Support',          'Infrastructure, Systems and Internal IT')
) AS v(dept_name, team_name)
JOIN Departments d ON d.name = v.dept_name AND d.company_id = @company_id
WHERE NOT EXISTS (
    SELECT 1 FROM Teams t WHERE t.department_id = d.id AND t.name = v.team_name
);

-- ---------------------------------------------------------------------------
-- Roles
-- ---------------------------------------------------------------------------
-- Joined through Departments as well as Teams: Teams is unique on
-- (department_id, name), not on name alone, so a future 'Leadership' team in two
-- departments would otherwise match twice and duplicate every role under it.
INSERT INTO Roles (team_id, title, level, access_role)
SELECT t.id, v.title, v.level, v.access_role
FROM (VALUES
    -- Human Resources
    ('Human Resources', 'HR Leadership',      'HR Lead',                        0, 'executive'),
    ('Human Resources', 'Talent Acquisition', 'Talent Acquisition Manager',     1, 'manager'),
    ('Human Resources', 'Talent Acquisition', 'Senior Recruiter',               2, 'employee'),
    ('Human Resources', 'Talent Acquisition', 'Recruiter',                      3, 'employee'),
    ('Human Resources', 'People Operations',  'People Operations Coordinator',  2, 'employee'),
    ('Human Resources', 'Accounting',         'Senior Accountant',              2, 'employee'),
    ('Human Resources', 'Accounting',         'Accountant',                     3, 'employee'),
    ('Human Resources', 'Accounting',         'Accounting Coordinator',         3, 'employee'),
    ('Human Resources', 'Financial Planning', 'Senior Financial Analyst',       2, 'employee'),
    ('Human Resources', 'Financial Planning', 'Financial Analyst',              3, 'employee'),

    -- Software Development
    ('Software Development', 'Engineering Leadership', 'Chief Technology Officer',        0, 'executive'),
    ('Software Development', 'Cybersecurity',          'Director of Cybersecurity',       1, 'director'),
    ('Software Development', 'Cybersecurity',          'Senior Security Architect',       2, 'employee'),
    ('Software Development', 'Cybersecurity',          'Information Security Engineer',   3, 'employee'),
    ('Software Development', 'Cybersecurity',          'Security Analyst',                4, 'employee'),
    ('Software Development', 'Software Engineering',   'Director of Software Engineering', 1, 'director'),
    ('Software Development', 'Software Engineering',   'Software Engineering Manager',    2, 'manager'),
    ('Software Development', 'Software Engineering',   'SDE 3',                           3, 'employee'),
    ('Software Development', 'Software Engineering',   'SDE 2',                           4, 'employee'),
    ('Software Development', 'Software Engineering',   'SDE 1',                           5, 'employee'),
    ('Software Development', 'Software Engineering',   'Engineering Intern',              6, 'employee'),
    ('Software Development', 'DevOps',                 'Director of DevOps',              1, 'director'),
    ('Software Development', 'DevOps',                 'Senior DevOps',                   2, 'employee'),
    ('Software Development', 'DevOps',                 'DevOps Engineer',                 3, 'employee'),
    ('Software Development', 'DevOps',                 'Junior DevOps',                   4, 'employee'),

    -- Sales
    ('Sales', 'Sales Leadership',        'Sales Lead',                          0, 'executive'),
    ('Sales', 'New Business',            'VP of Sales',                         1, 'director'),
    ('Sales', 'New Business',            'Senior Account Executive',            2, 'employee'),
    ('Sales', 'New Business',            'Account Executive',                   3, 'employee'),
    ('Sales', 'Client and Revenue Ops',  'Director of Client and Revenue Ops',  1, 'director'),
    ('Sales', 'Client and Revenue Ops',  'Customer Success Manager',            2, 'manager'),
    ('Sales', 'Client and Revenue Ops',  'Sales Specialist',                    2, 'employee'),
    ('Sales', 'Client and Revenue Ops',  'Customer Success Associate',          3, 'employee'),

    -- IT Support
    ('IT Support', 'IT Leadership', 'IT Support Lead',                                0, 'executive'),
    ('IT Support', 'Help Desk',     'IT Support Manager',                             1, 'manager'),
    ('IT Support', 'Help Desk',     'Senior IT Support Specialist',                   2, 'employee'),
    ('IT Support', 'Help Desk',     'IT Support Specialist',                          3, 'employee'),
    ('IT Support', 'Help Desk',     'Helpdesk Technician',                            4, 'employee'),
    ('IT Support', 'Help Desk',     'End User Support',                               4, 'employee'),
    ('IT Support', 'Infrastructure, Systems and Internal IT', 'Senior IT Engineer',          2, 'employee'),
    ('IT Support', 'Infrastructure, Systems and Internal IT', 'Senior Systems Administrator', 2, 'employee'),
    ('IT Support', 'Infrastructure, Systems and Internal IT', 'System Administrator',        3, 'employee')
) AS v(dept_name, team_name, title, level, access_role)
JOIN Departments d ON d.name = v.dept_name AND d.company_id = @company_id
JOIN Teams t       ON t.department_id = d.id AND t.name = v.team_name
WHERE NOT EXISTS (
    SELECT 1 FROM Roles r WHERE r.team_id = t.id AND r.title = v.title
);

DECLARE @dept_count INT, @team_count INT, @role_count INT;

SELECT @dept_count = COUNT(*) FROM Departments WHERE company_id = @company_id;

SELECT @team_count = COUNT(*) FROM Teams t
    JOIN Departments d ON d.id = t.department_id
    WHERE d.company_id = @company_id;

SELECT @role_count = COUNT(*) FROM Roles r
    JOIN Teams t ON t.id = r.team_id
    JOIN Departments d ON d.id = t.department_id
    WHERE d.company_id = @company_id;

PRINT 'org_seed: ' + CAST(@dept_count AS VARCHAR) + ' departments, '
    + CAST(@team_count AS VARCHAR) + ' teams, '
    + CAST(@role_count AS VARCHAR) + ' roles.';
