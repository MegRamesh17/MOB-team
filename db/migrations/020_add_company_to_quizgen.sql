-- 020_add_company_to_quizgen.sql
--
-- Puts company_id on every quizgen table, so a query can be scoped to one tenant.
--
-- THE GAP THIS CLOSES
-- 009_add_multitenancy.sql added Companies and put company_id on Departments, Employees
-- and Courses. The quizgen tables came later (011) and never got it. So today every
-- chunk, question, attempt, response and certificate is visible to every company that
-- queries the database — invisible while one company's data is in there, and a
-- straightforward data leak the moment there are two.
--
-- WHY DENORMALISED RATHER THAN JOINED
-- company_id could be reached by joining: responses -> attempts -> Employees.company_id,
-- questions -> SourceChunks -> ... The relationships exist. It is still the wrong choice
-- here, for one reason: the failure mode is a FORGOTTEN FILTER, and it fails silently by
-- returning someone else's rows.
--
-- With a column on every table, "does this query scope by tenant?" is answerable by
-- reading the query, and auditable with grep. With joins it is answerable only by
-- following the relationship chain in your head, and a query that forgets one still runs,
-- still returns rows, and still looks correct in review.
--
-- The cost is the usual denormalisation cost: company_id must be written correctly on
-- insert, and could in principle disagree with the join path. That is a bug you can test
-- for. A missing filter is a bug you discover from a customer.
--
-- NULLABLE FIRST, THEN BACKFILL, THEN TIGHTEN — the same shape 009 used, so existing rows
-- have somewhere to go before the constraint applies.

-- ---------------------------------------------------------------------------
-- 1. Add the columns
-- ---------------------------------------------------------------------------
IF COL_LENGTH('dbo.SourceChunks', 'company_id') IS NULL
    ALTER TABLE dbo.SourceChunks ADD company_id INT NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuestions', 'company_id') IS NULL
    ALTER TABLE dbo.GeneratedQuestions ADD company_id INT NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuizAttempts', 'company_id') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttempts ADD company_id INT NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuizResponses', 'company_id') IS NULL
    ALTER TABLE dbo.GeneratedQuizResponses ADD company_id INT NULL;
GO
IF COL_LENGTH('dbo.Certificates', 'company_id') IS NULL
    ALTER TABLE dbo.Certificates ADD company_id INT NULL;
GO

-- GeneratedOptions and GeneratedAnswerKeys are deliberately NOT given a column. They are
-- reachable only through a question_id, and every query that reads them has already
-- scoped the question. Adding company_id there would be two more places to keep in sync
-- for no additional protection.

-- ---------------------------------------------------------------------------
-- 2. Backfill
-- ---------------------------------------------------------------------------
-- Everything already in the database belongs to the company 009 seeded. Resolved by name
-- rather than assuming id 1, because an IDENTITY value is not something to hardcode.
DECLARE @default_company INT =
    (SELECT TOP 1 id FROM dbo.Companies ORDER BY id);

IF @default_company IS NULL
BEGIN
    RAISERROR('Companies is empty — run 009_add_multitenancy.sql and the seed first.', 16, 1);
    RETURN;
END

UPDATE dbo.SourceChunks           SET company_id = @default_company WHERE company_id IS NULL;
UPDATE dbo.GeneratedQuestions     SET company_id = @default_company WHERE company_id IS NULL;
UPDATE dbo.GeneratedQuizAttempts  SET company_id = @default_company WHERE company_id IS NULL;
UPDATE dbo.GeneratedQuizResponses SET company_id = @default_company WHERE company_id IS NULL;
UPDATE dbo.Certificates           SET company_id = @default_company WHERE company_id IS NULL;
GO

-- ---------------------------------------------------------------------------
-- 3. Tighten
-- ---------------------------------------------------------------------------
-- NOT NULL is the point of the exercise. A nullable tenant column means a row can be
-- written with no owner, and a row with no owner is a row that no filter excludes —
-- exactly the "visible to everybody" default this migration exists to remove.
ALTER TABLE dbo.SourceChunks           ALTER COLUMN company_id INT NOT NULL;
GO
ALTER TABLE dbo.GeneratedQuestions     ALTER COLUMN company_id INT NOT NULL;
GO
ALTER TABLE dbo.GeneratedQuizAttempts  ALTER COLUMN company_id INT NOT NULL;
GO
ALTER TABLE dbo.GeneratedQuizResponses ALTER COLUMN company_id INT NOT NULL;
GO
ALTER TABLE dbo.Certificates           ALTER COLUMN company_id INT NOT NULL;
GO

-- ---------------------------------------------------------------------------
-- 4. Foreign keys and indexes
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_SourceChunks_Company')
    ALTER TABLE dbo.SourceChunks ADD CONSTRAINT FK_SourceChunks_Company
        FOREIGN KEY (company_id) REFERENCES dbo.Companies(id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_GeneratedQuestions_Company')
    ALTER TABLE dbo.GeneratedQuestions ADD CONSTRAINT FK_GeneratedQuestions_Company
        FOREIGN KEY (company_id) REFERENCES dbo.Companies(id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_QuizAttempts_Company')
    ALTER TABLE dbo.GeneratedQuizAttempts ADD CONSTRAINT FK_QuizAttempts_Company
        FOREIGN KEY (company_id) REFERENCES dbo.Companies(id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_QuizResponses_Company')
    ALTER TABLE dbo.GeneratedQuizResponses ADD CONSTRAINT FK_QuizResponses_Company
        FOREIGN KEY (company_id) REFERENCES dbo.Companies(id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Certificates_Company')
    ALTER TABLE dbo.Certificates ADD CONSTRAINT FK_Certificates_Company
        FOREIGN KEY (company_id) REFERENCES dbo.Companies(id);
GO

-- Leading with company_id: every query filters on it, so it belongs first in the key.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_SourceChunks_Company')
    CREATE INDEX IX_SourceChunks_Company ON dbo.SourceChunks(company_id, role_scope);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_GeneratedQuestions_Company')
    CREATE INDEX IX_GeneratedQuestions_Company
        ON dbo.GeneratedQuestions(company_id, review_status);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QuizResponses_Company')
    CREATE INDEX IX_QuizResponses_Company
        ON dbo.GeneratedQuizResponses(company_id, learner_id, topic);
GO

-- ---------------------------------------------------------------------------
-- 5. The mastery view has to filter too
-- ---------------------------------------------------------------------------
-- Easy to miss: a view is a stored query, and this one aggregates responses across every
-- learner. Without company_id it would keep serving cross-tenant rows however carefully
-- the callers were scoped — the filter would be applied to a result that had already
-- merged two companies' answers.
CREATE OR ALTER VIEW dbo.vw_LearnerTopicMastery
AS
SELECT
    r.company_id,
    r.learner_id,
    r.topic,
    COUNT(*)                                   AS answered,
    SUM(CAST(r.is_correct AS INT))             AS correct,
    CAST(100.0 * SUM(CAST(r.is_correct AS INT)) / COUNT(*) AS DECIMAL(5,2)) AS accuracy_percent,
    CASE
        WHEN 100.0 * SUM(CAST(r.is_correct AS INT)) / COUNT(*) >= 85 THEN 'Expert'
        WHEN 100.0 * SUM(CAST(r.is_correct AS INT)) / COUNT(*) >= 60 THEN 'Mediocre'
        ELSE 'Beginner'
    END AS mastery_level
FROM dbo.GeneratedQuizResponses AS r
GROUP BY r.company_id, r.learner_id, r.topic;
GO

-- Same for the answer-safe serving view.
CREATE OR ALTER VIEW dbo.vw_ServableQuestions
AS
SELECT
    q.question_id,
    q.company_id,
    q.topic,
    q.question_type,
    q.difficulty,
    q.prompt,
    q.points,
    q.provenance_class,
    c.role_scope,
    o.option_id,
    o.option_text,
    o.sort_order
FROM dbo.GeneratedQuestions AS q
LEFT JOIN dbo.SourceChunks   AS c ON c.chunk_id = q.source_chunk_id
LEFT JOIN dbo.GeneratedOptions AS o ON o.question_id = q.question_id
WHERE q.review_status = 'Approved';
GO
