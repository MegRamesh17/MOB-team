-- 028_create_skill_interest.sql
--
-- What an employee said they want to learn, and whether they've been asked yet.
--
-- WHY A SEPARATE TABLE, NOT A COLUMN ON EMPLOYEES
-- An employee can express interest in more than one existing training, so this is a
-- one-to-many relationship (Employees -> interests), not a single value. Keeping it as
-- its own table also means "recommended" is computed the same way "required" already is
-- elsewhere in this file's serving code -- a join/lookup against a small table -- rather
-- than parsing a delimited string out of a column.
--
-- WHY doc_title AND NOT A FOREIGN KEY TO A TRAININGS TABLE
-- There is no Trainings table -- "training" is COALESCE(source_doc_title, topic) on
-- dbo.GeneratedQuestions, derived at query time (see list_trainings in function_app.py).
-- Every other place in this codebase that refers to a training by identity uses its
-- doc_title as a plain string for the same reason (RoleRequirements, Certificates). This
-- follows that established pattern rather than introducing a different one.
--
-- WHY skills_prompted_at IS ON Employees, NOT INFERRED FROM "HAS ANY ROW HERE"
-- An employee who is asked and picks nothing (or hits the dismiss button) has a real,
-- meaningful "already asked, said no" state -- distinct from "never asked". Inferring
-- the former from an empty EmployeeSkillInterest result would be indistinguishable from
-- the latter, and the popup would nag them every session.

IF COL_LENGTH('dbo.Employees', 'skills_prompted_at') IS NULL
    ALTER TABLE dbo.Employees ADD skills_prompted_at DATETIME2(3) NULL;
GO

IF OBJECT_ID('dbo.EmployeeSkillInterest', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.EmployeeSkillInterest
    (
        employee_id INT           NOT NULL,
        company_id  INT           NOT NULL,
        doc_title   NVARCHAR(300) NOT NULL,
        created_at  DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_EmployeeSkillInterest PRIMARY KEY (employee_id, doc_title),
        CONSTRAINT FK_EmployeeSkillInterest_Employee FOREIGN KEY (employee_id)
            REFERENCES dbo.Employees(id) ON DELETE CASCADE,
        CONSTRAINT FK_EmployeeSkillInterest_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id)
    );

    CREATE INDEX IX_EmployeeSkillInterest_Employee
        ON dbo.EmployeeSkillInterest(employee_id, company_id);
END
GO
