-- 002_create_courses.sql
-- Course catalog. role_required_for and resources are stored as JSON arrays
-- (T-SQL has no native array type). Use JSON_VALUE / OPENJSON to query them.

CREATE TABLE Courses (
    id                  INT IDENTITY(1,1) PRIMARY KEY,
    title               NVARCHAR(200)  NOT NULL,
    description         NVARCHAR(1000) NULL,
    role_required_for   NVARCHAR(MAX)  NULL,   -- JSON array, e.g. '["Sales Rep","Manager"]'
    is_mandatory        BIT            NOT NULL DEFAULT 1,
    validity_months     INT            NOT NULL DEFAULT 12,  -- how long a completion stays valid
    passing_bar_percent INT            NOT NULL DEFAULT 80,
    difficulty_level    NVARCHAR(20)   NOT NULL DEFAULT 'Beginner'
                        CHECK (difficulty_level IN ('Beginner', 'Mediocre', 'Expert')),
    estimated_minutes   INT            NOT NULL DEFAULT 30,   -- feeds the "expected time" timeline feature
    resources           NVARCHAR(MAX)  NULL,   -- JSON array of {type, title, url}
    created_at          DATETIME2      NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT CK_Courses_RoleRequiredFor_IsJson CHECK (ISJSON(role_required_for) = 1 OR role_required_for IS NULL),
    CONSTRAINT CK_Courses_Resources_IsJson CHECK (ISJSON(resources) = 1 OR resources IS NULL)
);

CREATE INDEX IX_Courses_Difficulty ON Courses(difficulty_level);
