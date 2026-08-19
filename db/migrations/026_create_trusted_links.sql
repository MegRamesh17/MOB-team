-- 026_create_trusted_links.sql
--
-- Track D: manager-submitted trusted reference URLs (Project Hub's TrustedLinks table).
-- Distinct from src/quizgen/registry.py's Source list, which is a separate, static,
-- dev-maintained allowlist used to build the AI Search corpus offline. This table is the
-- live, per-company record of what a manager registered through the product itself, and
-- is what POST /links/add and GET /links read and write.
--
-- scope = 'team': feeds only the manager's own reporting subtree, for the one role_code
-- they targeted -- validated the same way upload role targeting is (walk manager_id
-- recursively; see function_app.py's _permitted_upload_roles).
--
-- scope = 'company_wide': applies to everyone (role_code = 'ALL'). Only one may be
-- is_active = 1 per company at a time -- adding a new company-wide link retires the
-- previous one automatically (Decisions Log #4). Restricted to admin/executive, the same
-- tier that already governs company-wide targeting for uploads.
--
-- is_active lets a link be retired (superseded, or found stale) without losing the
-- history of what was once trusted -- deleting the row would erase which company-wide
-- policy source was in effect when a given question was generated.

IF OBJECT_ID('dbo.TrustedLinks', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.TrustedLinks
    (
        id          INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_TrustedLinks PRIMARY KEY,
        company_id  INT            NOT NULL,
        added_by    INT            NOT NULL,
        scope       NVARCHAR(20)   NOT NULL
            CONSTRAINT CK_TrustedLinks_Scope CHECK (scope IN ('team', 'company_wide')),
        -- The quizgen training role code (SDE2, SWE_MANAGER, ...), or 'ALL' for a
        -- company_wide link. Matches RoleRequirements.role_code (019) and the role_code
        -- claim in the session token -- not Roles.id, same reasoning as 019.
        role_code   NVARCHAR(40)   NOT NULL,
        url         NVARCHAR(1000) NOT NULL,
        is_active   BIT            NOT NULL DEFAULT 1,
        created_at  DATETIME2(3)   NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT FK_TrustedLinks_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id),
        CONSTRAINT FK_TrustedLinks_AddedBy FOREIGN KEY (added_by)
            REFERENCES dbo.Employees(id)
    );

    -- GET /links' main read: a company's currently active links.
    CREATE INDEX IX_TrustedLinks_Company ON dbo.TrustedLinks(company_id, is_active);

    -- Enforced in code (POST /links/add retires the previous row before inserting), not
    -- as a filtered unique index here: SQL Server allows only one filtered index
    -- predicate per column set, and this table needs the lookup above too. The
    -- code-level retire-then-insert is a single request under one connection, not a
    -- concurrent path the way QuizgenRoles.add_role's race was -- see api/shared/
    -- sqlbank.py's add_role for why that one needed a MERGE instead.
END
GO
