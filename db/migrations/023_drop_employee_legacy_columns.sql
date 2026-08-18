-- 023_drop_employee_legacy_columns.sql
--
-- Completes 007_alter_employees_add_role.sql's own deferred cleanup: role_legacy and
-- department_legacy were kept "temporarily... drop them once the app is fully cut over."
-- Nothing in the codebase references either column anymore -- the app is fully on
-- role_id. Left in place, seed_data.sql's INSERT INTO Employees (which only populates
-- role_id) fails on their leftover NOT NULL constraint:
--   "Cannot insert the value NULL into column 'role_legacy' ... column does not allow
--   nulls."
--
-- Guarded, so it's a no-op wherever they're already gone.
--
-- IX_Employees_Department (001_create_employees.sql) was built on the original
-- `department` column; 007's sp_rename carried the index along onto department_legacy
-- rather than dropping it, so it has to go first or DROP COLUMN fails with "index ...
-- is dependent on column 'department_legacy'". Nothing needs this index anymore --
-- department-based lookups now go through role_id -> Roles -> Teams -> Departments.

IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Employees_Department')
    DROP INDEX IX_Employees_Department ON dbo.Employees;
GO
IF COL_LENGTH('dbo.Employees', 'role_legacy') IS NOT NULL
    ALTER TABLE dbo.Employees DROP COLUMN role_legacy;
GO
IF COL_LENGTH('dbo.Employees', 'department_legacy') IS NOT NULL
    ALTER TABLE dbo.Employees DROP COLUMN department_legacy;
GO
