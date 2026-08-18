-- 025_create_quizgen_roles_and_jobs.sql
--
-- Two tables the deployed upload/generate path needs, that 011 never created because
-- nothing deployed used them yet.
--
-- QuizgenRoles: the company's role catalog, as the manager builds it via "Add role" in
-- the Documents screen. Mirrors the local SQLite bank's `roles` table (bank.py's
-- roles()/add_role()/remove_role()) -- 011's own header says it mirrors the SQLite bank
-- "so the loader is a copy rather than a translation", and this table was the one part
-- of that mirror it left out.
--
-- Deliberately separate from dbo.Roles (006, the org chart: title/level/access_role/
-- team_id). dbo.Roles answers "what jobs exist and who reports to whom" and role_codes.sql
-- maps some of those onto training codes. QuizgenRoles answers a narrower question: which
-- role codes exist to map an uploaded document's sections onto. A company can have a role
-- code here with nobody in it yet (SECURITY existed in SEED_ROLES before anyone held a
-- Cybersecurity title), and a manager can add one ad hoc that has no org-chart seat at
-- all. Merging the two tables would force every org-chart title to be a trainable role
-- and vice versa, which is not true in either direction.
--
-- GenerationJobs: status for one upload's question generation, polled by GET /jobs/{id}.
-- The local dev server tracks this in an in-memory dict on a background thread, which
-- does not survive across Azure Functions invocations -- a Linux App Service instance can
-- recycle or scale to a second instance between the request that starts generation and
-- the next poll, and an in-memory dict on the first instance is invisible to the second.
-- A row in the one durable store both instances share is what makes polling correct
-- rather than accidentally working under low traffic.

IF OBJECT_ID('dbo.QuizgenRoles', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.QuizgenRoles
    (
        role_code   NVARCHAR(40)  NOT NULL,
        company_id  INT           NOT NULL,
        title       NVARCHAR(200) NOT NULL,
        description NVARCHAR(500) NOT NULL DEFAULT '',
        created_at  DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_QuizgenRoles PRIMARY KEY (role_code, company_id),
        CONSTRAINT FK_QuizgenRoles_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id)
    );
END
GO

IF OBJECT_ID('dbo.GenerationJobs', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.GenerationJobs
    (
        job_id       NVARCHAR(40)  NOT NULL CONSTRAINT PK_GenerationJobs PRIMARY KEY,
        company_id   INT           NOT NULL,
        doc_title    NVARCHAR(300) NOT NULL,
        -- 'running' | 'done' | 'error' -- same three values the local dev server uses,
        -- so the frontend's poll loop (stop when state !== "running") needs no change.
        state        NVARCHAR(20)  NOT NULL DEFAULT 'running',
        total        INT           NOT NULL DEFAULT 0,
        done_count   INT           NOT NULL DEFAULT 0,
        kept         INT           NOT NULL DEFAULT 0,
        written      INT           NOT NULL DEFAULT 0,
        rejected     INT           NOT NULL DEFAULT 0,
        message      NVARCHAR(500) NOT NULL DEFAULT '',
        created_at   DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
        finished_at  DATETIME2(3)  NULL,

        CONSTRAINT CK_GenerationJobs_State CHECK (state IN ('running', 'done', 'error')),
        CONSTRAINT FK_GenerationJobs_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id)
    );

    CREATE INDEX IX_GenerationJobs_Company ON dbo.GenerationJobs(company_id, created_at DESC);
END
GO
