-- 022_repair_core_company_id.sql
--
-- Repairs the rest of the gap 021 only partly covered. 009_add_multitenancy.sql has no
-- guards and no GO separators, so it is ONE batch -- when its first statement
-- (CREATE TABLE Companies) failed, every statement after it in that same file never ran
-- either. 021 fixed the missing Companies table itself. It did not fix the rest of 009:
-- company_id was never added to Departments, Employees or Courses, so their backfill,
-- NOT NULL tightening, foreign keys and indexes never happened either.
--
-- SchemaMigrations still marked 009 as applied throughout (the same silent-failure bug
-- 021 and the migrate-database workflow fix address), so nothing ever retried this.
-- Confirmed live: db/seed/org_seed.sql fails with "Invalid column name 'company_id'" on
-- Departments the moment it tries to insert a row.
--
-- Guarded like 020_add_company_to_quizgen.sql, so this is a no-op wherever 009 actually
-- completed. Mirrors 009's own unqualified table names (Departments, not dbo.Departments)
-- for consistency with the migration it's completing.

-- ---------------------------------------------------------------------------
-- 1. Add the columns
-- ---------------------------------------------------------------------------
IF COL_LENGTH('dbo.Departments', 'company_id') IS NULL
    ALTER TABLE Departments ADD company_id INT NULL;
GO
IF COL_LENGTH('dbo.Employees', 'company_id') IS NULL
    ALTER TABLE Employees ADD company_id INT NULL;
GO
IF COL_LENGTH('dbo.Courses', 'company_id') IS NULL
    ALTER TABLE Courses ADD company_id INT NULL;
GO

-- ---------------------------------------------------------------------------
-- 2. Backfill
-- ---------------------------------------------------------------------------
-- Resolved by name rather than assuming id 1, same reasoning as 020: an IDENTITY value
-- is not something to hardcode.
DECLARE @default_company INT = (SELECT TOP 1 id FROM dbo.Companies ORDER BY id);

IF @default_company IS NULL
BEGIN
    RAISERROR('Companies is empty -- run 009_add_multitenancy.sql / 021_repair_companies_table.sql first.', 16, 1);
    RETURN;
END

UPDATE Departments SET company_id = @default_company WHERE company_id IS NULL;
UPDATE Employees   SET company_id = @default_company WHERE company_id IS NULL;
UPDATE Courses     SET company_id = @default_company WHERE company_id IS NULL;
GO

-- ---------------------------------------------------------------------------
-- 3. Tighten
-- ---------------------------------------------------------------------------
ALTER TABLE Departments ALTER COLUMN company_id INT NOT NULL;
GO
ALTER TABLE Employees ALTER COLUMN company_id INT NOT NULL;
GO
ALTER TABLE Courses ALTER COLUMN company_id INT NOT NULL;
GO

-- ---------------------------------------------------------------------------
-- 4. Foreign keys and indexes
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Departments_Company')
    ALTER TABLE Departments ADD CONSTRAINT FK_Departments_Company FOREIGN KEY (company_id) REFERENCES Companies(id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Employees_Company')
    ALTER TABLE Employees ADD CONSTRAINT FK_Employees_Company FOREIGN KEY (company_id) REFERENCES Companies(id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Courses_Company')
    ALTER TABLE Courses ADD CONSTRAINT FK_Courses_Company FOREIGN KEY (company_id) REFERENCES Companies(id);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Departments_Company')
    CREATE INDEX IX_Departments_Company ON Departments(company_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Employees_Company')
    CREATE INDEX IX_Employees_Company ON Employees(company_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Courses_Company')
    CREATE INDEX IX_Courses_Company ON Courses(company_id);
GO
