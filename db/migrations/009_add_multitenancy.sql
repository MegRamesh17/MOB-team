-- 009_add_multitenancy.sql
-- Adds the multi-tenant layer: a Companies table as the root, with
-- company_id added to the tables that need direct filtering. Teams and
-- Roles don't need their own company_id -- they inherit it through
-- department_id/team_id, since a Team can't exist without a Department
-- that already has one.

CREATE TABLE Companies (
    id            INT IDENTITY(1,1) PRIMARY KEY,
    name          NVARCHAR(200) NOT NULL,
    industry      NVARCHAR(100) NULL,
    culture_notes NVARCHAR(MAX) NULL,   -- free text: tone/values, feeds AI prompts
    created_at    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

-- Seed the one company that already has data, so existing rows have
-- somewhere to attach once company_id is added below.
INSERT INTO Companies (name, industry) VALUES ('Quadrant Technologies', 'Technology');

ALTER TABLE Departments ADD company_id INT NULL;
ALTER TABLE Employees   ADD company_id INT NULL;
ALTER TABLE Courses     ADD company_id INT NULL;

-- Backfill everything that already exists to point at Quadrant (id 1)
UPDATE Departments SET company_id = 1;
UPDATE Employees   SET company_id = 1;
UPDATE Courses      SET company_id = 1;

-- Now that every row has a value, make it required and add the FK
ALTER TABLE Departments ALTER COLUMN company_id INT NOT NULL;
ALTER TABLE Employees   ALTER COLUMN company_id INT NOT NULL;
ALTER TABLE Courses     ALTER COLUMN company_id INT NOT NULL;

ALTER TABLE Departments ADD CONSTRAINT FK_Departments_Company FOREIGN KEY (company_id) REFERENCES Companies(id);
ALTER TABLE Employees   ADD CONSTRAINT FK_Employees_Company   FOREIGN KEY (company_id) REFERENCES Companies(id);
ALTER TABLE Courses     ADD CONSTRAINT FK_Courses_Company     FOREIGN KEY (company_id) REFERENCES Companies(id);

CREATE INDEX IX_Departments_Company ON Departments(company_id);
CREATE INDEX IX_Employees_Company ON Employees(company_id);
CREATE INDEX IX_Courses_Company ON Courses(company_id);
