-- 007_alter_employees_add_role.sql
-- Points Employees at the new normalized Roles table instead of free-text
-- role/department strings. Old columns kept temporarily as *_legacy for a
-- safe migration -- drop them once the app is fully cut over.

EXEC sp_rename 'Employees.role', 'role_legacy', 'COLUMN';
EXEC sp_rename 'Employees.department', 'department_legacy', 'COLUMN';

ALTER TABLE Employees ADD role_id INT NULL;

ALTER TABLE Employees ADD CONSTRAINT FK_Employees_Role
    FOREIGN KEY (role_id) REFERENCES Roles(id);

CREATE INDEX IX_Employees_Role ON Employees(role_id);

-- After backfilling role_id for every employee (done in app code or a
-- one-off script that matches role_legacy text to Roles.title), run:
--   ALTER TABLE Employees ALTER COLUMN role_id INT NOT NULL;
--   ALTER TABLE Employees DROP COLUMN role_legacy, department_legacy;
