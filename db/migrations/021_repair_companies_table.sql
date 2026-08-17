-- 021_repair_companies_table.sql
--
-- Repairs a gap left by 009_add_multitenancy.sql: dbo.SchemaMigrations recorded 009 as
-- applied, but dbo.Companies never actually got created (009 has no guards, so a
-- mid-batch failure aborted the CREATE TABLE without ever surfacing to the migration
-- runner -- see the fix in .github/workflows/migrate-database.yml in this same change).
-- Every table with company_id (009, 020) has a dangling FK to a table that doesn't
-- exist, which is why 020 fails with "Invalid object name 'dbo.Companies'".
--
-- Guarded like 011, so it is a no-op wherever 009 actually did create Companies.

IF OBJECT_ID('dbo.Companies', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.Companies
    (
        id            INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Companies PRIMARY KEY,
        name          NVARCHAR(200) NOT NULL,
        industry      NVARCHAR(100) NULL,
        culture_notes NVARCHAR(MAX) NULL,
        created_at    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
    );

    -- Same seed 009 intended: the one company that already has data, so
    -- Departments.company_id / Employees.company_id / Courses.company_id -- all
    -- backfilled to 1 by 009 -- have somewhere real to point.
    INSERT INTO dbo.Companies (name, industry) VALUES ('Quadrant Technologies', 'Technology');
END
GO
