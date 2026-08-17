-- 019_create_role_requirements.sql
--
-- Which trainings each role must complete.
--
-- This is Coverage's DENOMINATOR. Without it, "7 of 7 done" has no 7 and a Q Score
-- cannot be computed at all — the screen shows 0 for everyone and looks broken rather
-- than unconfigured.
--
-- WHY A TABLE AND NOT DERIVED
-- It would be easy to infer the required list from whatever documents are scoped to a
-- role. That would be wrong: uploading a document would silently lower everyone's Q
-- Score, and deleting one would raise it. A compliance number that moves because someone
-- filed a PDF is not measuring compliance. So this is set deliberately, by an admin, and
-- changes only when someone decides it should.
--
-- Deliberately NOT reusing CourseRoles (008). That maps Courses rows to Roles; quizgen
-- certifies source documents, which have no Courses row. Same idea, different grain —
-- and folding them together would mean one of the two had to lie about what it points at.

IF OBJECT_ID('dbo.RoleRequirements', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.RoleRequirements
    (
        -- The quizgen training role code (SDE2, SWE_MANAGER, ALL), matching
        -- Roles.role_code from 016_add_role_code.sql and the role_code claim in the
        -- session token. NOT Roles.id: the requirement is about a role code, and a
        -- company can have several Roles rows sharing one code across teams.
        role_code   NVARCHAR(40)  NOT NULL,
        doc_title   NVARCHAR(300) NOT NULL,
        category    NVARCHAR(20)  NOT NULL
            CONSTRAINT DF_RoleRequirements_Category DEFAULT 'technical'
            CONSTRAINT CK_RoleRequirements_Category CHECK (category IN ('behavioural', 'technical')),
        company_id  INT           NOT NULL DEFAULT 1,
        created_at  DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_RoleRequirements PRIMARY KEY (company_id, role_code, doc_title)
    );

    -- The lookup on every Q Score read: "what does this role owe?"
    CREATE INDEX IX_RoleRequirements_Role
        ON dbo.RoleRequirements(company_id, role_code);
END
GO
