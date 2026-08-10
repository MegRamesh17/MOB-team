-- 001_create_employees.sql
-- Core employee table. manager_id self-references Employees for the org hierarchy
-- (used by the Manager Dashboard to find "my team").

CREATE TABLE Employees (
    id             INT IDENTITY(1,1) PRIMARY KEY,
    name           NVARCHAR(100)  NOT NULL,
    email          NVARCHAR(150)  NOT NULL UNIQUE,
    role           NVARCHAR(100)  NOT NULL,        -- e.g. 'Sales Rep', 'Software Engineer'
    department     NVARCHAR(100)  NOT NULL,        -- e.g. 'Sales', 'Engineering'
    manager_id     INT            NULL,
    mastery_level  NVARCHAR(20)   NOT NULL DEFAULT 'Beginner'
                   CHECK (mastery_level IN ('Beginner', 'Mediocre', 'Expert')),
    mastery_override BIT         NOT NULL DEFAULT 0,  -- 1 = manager manually set the level
    goal           NVARCHAR(200) NULL,               -- free-text career/skill goal, used by AI recs
    created_at     DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT FK_Employees_Manager FOREIGN KEY (manager_id)
        REFERENCES Employees(id)
);

CREATE INDEX IX_Employees_Manager ON Employees(manager_id);
CREATE INDEX IX_Employees_Department ON Employees(department);
