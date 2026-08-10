-- org_seed.sql
-- Run after 006/007 migrations, before employees_seed.
-- access_role logic: exec/director titles -> 'executive'/'director',
-- anyone with "Manager" in the title -> 'manager', everyone else -> 'employee'.
-- Coordinators/Analysts are 'employee' even though senior in experience --
-- access_role is about system permissions, not seniority.

-- =========================================================
-- DEPARTMENTS (company_id 1 = Quadrant Technologies, seeded in 009)
-- =========================================================
INSERT INTO Departments (name, company_id) VALUES
('Human Resources', 1), ('Software Development', 1), ('Sales', 1), ('IT Support', 1);

-- =========================================================
-- TEAMS (each department gets a Leadership team + its named sub-functions)
-- =========================================================
INSERT INTO Teams (department_id, name) VALUES
(1, 'HR Leadership'), (1, 'Talent Acquisition'), (1, 'People Operations'),
(1, 'Accounting'), (1, 'Financial Planning'),
(2, 'Engineering Leadership'), (2, 'Cybersecurity'), (2, 'Software Engineering'), (2, 'DevOps'),
(3, 'Sales Leadership'), (3, 'New Business'), (3, 'Client and Revenue Ops'),
(4, 'IT Leadership'), (4, 'Help Desk'), (4, 'Infrastructure, Systems and Internal IT');

-- =========================================================
-- ROLES (team_id references the insert order above: 1=HR Leadership ... 15=Infra/Systems)
-- =========================================================

-- HR Leadership (team 1)
INSERT INTO Roles (team_id, title, level, access_role) VALUES (1, 'HR Lead', 0, 'executive');

-- Talent Acquisition (team 2)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(2, 'Talent Acquisition Manager', 1, 'manager'),
(2, 'Senior Recruiter', 2, 'employee'),
(2, 'Recruiter', 3, 'employee');

-- People Operations (team 3)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(3, 'People Operations Coordinator', 2, 'employee');

-- Accounting (team 4)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(4, 'Accounting Coordinator', 3, 'employee'),
(4, 'Senior Accountant', 2, 'employee'),
(4, 'Accountant', 3, 'employee');

-- Financial Planning (team 5)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(5, 'Senior Financial Analyst', 2, 'employee'),
(5, 'Financial Analyst', 3, 'employee');

-- Engineering Leadership (team 6)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(6, 'Chief Technology Officer', 0, 'executive');

-- Cybersecurity (team 7)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(7, 'Director of Cybersecurity', 1, 'director'),
(7, 'Senior Security Architect', 2, 'employee'),
(7, 'Information Security Engineer', 3, 'employee'),
(7, 'Security Analyst', 4, 'employee');

-- Software Engineering (team 8)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(8, 'Director of Software Engineering', 1, 'director'),
(8, 'Software Engineering Manager', 2, 'manager'),
(8, 'SDE 3', 3, 'employee'),
(8, 'SDE 2', 4, 'employee'),
(8, 'SDE 1', 5, 'employee');

-- DevOps (team 9)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(9, 'Director of DevOps', 1, 'director'),
(9, 'Senior DevOps', 2, 'employee'),
(9, 'DevOps Engineer', 3, 'employee'),
(9, 'Junior DevOps', 4, 'employee');

-- Sales Leadership (team 10)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(10, 'Sales Lead', 0, 'executive');

-- New Business (team 11)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(11, 'VP of Sales', 1, 'director'),
(11, 'Senior Account Executive', 2, 'employee'),
(11, 'Account Executive', 3, 'employee');

-- Client and Revenue Ops (team 12)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(12, 'Director of Client and Revenue Ops', 1, 'director'),
(12, 'Sales Specialist', 2, 'employee'),
(12, 'Customer Success Manager', 2, 'manager'),
(12, 'Customer Success Associate', 3, 'employee');

-- IT Leadership (team 13)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(13, 'IT Support Lead', 0, 'executive');

-- Help Desk (team 14)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(14, 'IT Support Manager', 1, 'manager'),
(14, 'Senior IT Support Specialist', 2, 'employee'),
(14, 'IT Support Specialist', 3, 'employee'),
(14, 'Helpdesk Technician', 4, 'employee'),
(14, 'End User Support', 4, 'employee');

-- Infrastructure, Systems and Internal IT (team 15)
INSERT INTO Roles (team_id, title, level, access_role) VALUES
(15, 'Senior IT Engineer', 2, 'employee'),
(15, 'Senior Systems Administrator', 2, 'employee'),
(15, 'System Administrator', 3, 'employee');
