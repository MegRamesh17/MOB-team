-- 006_create_org_structure.sql
-- Normalizes the org chart: Department > Team (sub-function) > Role (title + seniority).
-- access_role is the small RBAC set derived from title/level -- this is what
-- actually gates permissions in the app. level is a raw seniority rank used
-- for org display and for "recommend next role/training" AI logic later.

CREATE TABLE Departments (
    id      INT IDENTITY(1,1) PRIMARY KEY,
    name    NVARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE Teams (
    id             INT IDENTITY(1,1) PRIMARY KEY,
    department_id  INT NOT NULL,
    name           NVARCHAR(100) NOT NULL,   -- e.g. 'Talent Acquisition', 'DevOps'

    CONSTRAINT FK_Teams_Department FOREIGN KEY (department_id) REFERENCES Departments(id),
    CONSTRAINT UQ_Teams_Dept_Name UNIQUE (department_id, name)
);

CREATE TABLE Roles (
    id           INT IDENTITY(1,1) PRIMARY KEY,
    team_id      INT NOT NULL,
    title        NVARCHAR(150) NOT NULL,          -- e.g. 'Senior DevOps', 'SDE 1'
    level        INT NOT NULL,                    -- 0 = most senior in dept, higher = more junior
    access_role  NVARCHAR(20) NOT NULL DEFAULT 'employee'
                 CHECK (access_role IN ('employee', 'manager', 'director', 'admin', 'executive')),

    CONSTRAINT FK_Roles_Team FOREIGN KEY (team_id) REFERENCES Teams(id)
);

CREATE INDEX IX_Roles_Team ON Roles(team_id);
CREATE INDEX IX_Roles_AccessRole ON Roles(access_role);
