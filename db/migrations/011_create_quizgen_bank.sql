-- 011_create_quizgen_bank.sql
--
-- Tables for the quizgen pipeline (src/quizgen/). Mirrors the local SQLite bank so the
-- loader is a copy rather than a translation.
--
-- Why these and not 003_create_quizquestions / 010_create_readings_and_generated_quiz:
-- those were designed around content_agent.py, which sends a whole blob container to
-- the model in one prompt. That approach cannot say which document or page an answer
-- came from, so neither table has anywhere to put a citation, a review status, or a
-- role scope. Those three columns are what make a generated question defensible when
-- someone disputes a failed certification.
--
-- 003 and 010 are left in place and unused. Nothing here drops them.
--
-- Every statement is guarded, so this file is safe to re-run.

-- ---------------------------------------------------------------------------
-- SourceChunks: a passage of source material, small enough to ground one question.
-- ---------------------------------------------------------------------------
-- Chunks are stored, not thrown away after generation. Keeping them is what allows
-- regenerating questions for a weak topic later without re-reading any PDF, and what
-- lets a reviewer see the passage a question came from.
IF OBJECT_ID('dbo.SourceChunks', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.SourceChunks
    (
        chunk_id      NVARCHAR(64)   NOT NULL CONSTRAINT PK_SourceChunks PRIMARY KEY,
        doc_id        NVARCHAR(64)   NOT NULL,
        doc_title     NVARCHAR(300)  NOT NULL,
        section       NVARCHAR(300)  NOT NULL,
        topic         NVARCHAR(120)  NOT NULL,
        page_start    INT            NOT NULL DEFAULT 1,
        page_end      INT            NOT NULL DEFAULT 1,
        chunk_text    NVARCHAR(MAX)  NOT NULL,

        -- Which blob container the document was filed in, and which role that implies.
        -- The container layout IS the role taxonomy: company-docs applies to everyone,
        -- software-engineering-docs is split per document into SDE1/SDE2/SDE3/
        -- SWE_MANAGER/SWE_DIRECTOR. That beats inferring roles from the prose because
        -- it reflects a filing decision a human made.
        container     NVARCHAR(120)  NOT NULL DEFAULT '',
        role_scope    NVARCHAR(40)   NOT NULL DEFAULT 'ALL',

        -- 'document' today; 'web' once online sourcing lands. A web-sourced passage
        -- flows through the same generator and checks, so only these columns differ.
        source_type   NVARCHAR(20)   NOT NULL DEFAULT 'document',
        source_url    NVARCHAR(1000) NULL,
        fetched_at    DATETIME2(3)   NULL,   -- when retrieved; drives staleness for renewals

        created_at    DATETIME2(3)   NOT NULL DEFAULT SYSUTCDATETIME()
    );

    CREATE INDEX IX_SourceChunks_Role  ON dbo.SourceChunks(role_scope, topic);
    CREATE INDEX IX_SourceChunks_Doc   ON dbo.SourceChunks(doc_id);
END
GO

-- ---------------------------------------------------------------------------
-- GeneratedQuestions
-- ---------------------------------------------------------------------------
IF OBJECT_ID('dbo.GeneratedQuestions', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.GeneratedQuestions
    (
        question_id       NVARCHAR(64)   NOT NULL CONSTRAINT PK_GeneratedQuestions PRIMARY KEY,
        topic             NVARCHAR(120)  NOT NULL,
        question_type     NVARCHAR(20)   NOT NULL,
        difficulty        NVARCHAR(20)   NOT NULL DEFAULT 'Medium',
        prompt            NVARCHAR(1000) NOT NULL,
        explanation       NVARCHAR(1000) NULL,
        points            INT            NOT NULL DEFAULT 1,

        -- Provenance. Without these you cannot audit why a question exists, show the
        -- learner what they missed, or regenerate when the source changes.
        source_chunk_id   NVARCHAR(64)   NULL,
        source_doc_title  NVARCHAR(300)  NULL,
        source_page       INT            NULL,
        source_quote      NVARCHAR(MAX)  NULL,
        source_url        NVARCHAR(1000) NULL,   -- ExternalSource questions
        source_fetched_at DATETIME2(3)   NULL,

        -- 'Documented'     - traceable to a company document; MAY state company policy
        -- 'ExternalSource' - cited to a URL; may NOT state company policy
        -- 'RoleKnowledge'  - model inference, no source; may NOT state company policy
        provenance_class  NVARCHAR(20)   NOT NULL DEFAULT 'Documented',
        role_code         NVARCHAR(40)   NULL,
        role_requirement  NVARCHAR(500)  NULL,

        -- The gate. Nothing reaches a learner until a human approves it: a question
        -- with a wrong answer key certifies people on false information, and "the model
        -- wrote it" is not a defence. Quiz assembly reads Approved only.
        review_status     NVARCHAR(20)   NOT NULL DEFAULT 'PendingReview',
        reviewed_by       NVARCHAR(200)  NULL,
        reviewed_at       DATETIME2(3)   NULL,
        contradiction_notes NVARCHAR(MAX) NULL,  -- conflicts found against other documents

        generator         NVARCHAR(60)   NULL,   -- 'azure-openai' | 'mock'

        -- Measured difficulty. A model's guess at whether a question is hard is
        -- unreliable; the observed pass rate is not. Selection prefers these.
        times_served      INT            NOT NULL DEFAULT 0,
        times_correct     INT            NOT NULL DEFAULT 0,

        created_at        DATETIME2(3)   NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT FK_GeneratedQuestions_Chunk FOREIGN KEY (source_chunk_id)
            REFERENCES dbo.SourceChunks(chunk_id),
        CONSTRAINT CK_GeneratedQuestions_Type CHECK
            (question_type IN ('MultipleChoice', 'MultiSelect', 'TrueFalse', 'FillInBlank')),
        CONSTRAINT CK_GeneratedQuestions_Difficulty CHECK
            (difficulty IN ('Easy', 'Medium', 'Hard')),
        CONSTRAINT CK_GeneratedQuestions_Provenance CHECK
            (provenance_class IN ('Documented', 'ExternalSource', 'RoleKnowledge')),
        CONSTRAINT CK_GeneratedQuestions_Review CHECK
            (review_status IN ('PendingReview', 'Approved', 'Rejected')),
        CONSTRAINT CK_GeneratedQuestions_Stats CHECK
            (times_correct <= times_served)
    );

    -- Quiz assembly filters on exactly this.
    CREATE INDEX IX_GeneratedQuestions_Serve
        ON dbo.GeneratedQuestions(review_status, topic) INCLUDE (difficulty, points);
    CREATE INDEX IX_GeneratedQuestions_Chunk  ON dbo.GeneratedQuestions(source_chunk_id);
    CREATE INDEX IX_GeneratedQuestions_Review ON dbo.GeneratedQuestions(review_status, created_at);
END
GO

-- ---------------------------------------------------------------------------
-- GeneratedOptions
-- ---------------------------------------------------------------------------
-- Separate rows rather than a JSON blob, so a query can ask "which option did they
-- pick" and get a foreign key instead of parsing a string.
IF OBJECT_ID('dbo.GeneratedOptions', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.GeneratedOptions
    (
        option_id     NVARCHAR(64)  NOT NULL CONSTRAINT PK_GeneratedOptions PRIMARY KEY,
        question_id   NVARCHAR(64)  NOT NULL,
        option_text   NVARCHAR(500) NOT NULL,
        is_correct    BIT           NOT NULL DEFAULT 0,
        sort_order    INT           NOT NULL DEFAULT 0,

        CONSTRAINT FK_GeneratedOptions_Question FOREIGN KEY (question_id)
            REFERENCES dbo.GeneratedQuestions(question_id) ON DELETE CASCADE
    );

    CREATE INDEX IX_GeneratedOptions_Question ON dbo.GeneratedOptions(question_id, sort_order);
END
GO

-- ---------------------------------------------------------------------------
-- GeneratedAnswerKeys — accepted spellings for FillInBlank
-- ---------------------------------------------------------------------------
IF OBJECT_ID('dbo.GeneratedAnswerKeys', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.GeneratedAnswerKeys
    (
        question_id     NVARCHAR(64)  NOT NULL,
        accepted_answer NVARCHAR(300) NOT NULL,

        CONSTRAINT PK_GeneratedAnswerKeys PRIMARY KEY (question_id, accepted_answer),
        CONSTRAINT FK_GeneratedAnswerKeys_Question FOREIGN KEY (question_id)
            REFERENCES dbo.GeneratedQuestions(question_id) ON DELETE CASCADE
    );
END
GO

-- ---------------------------------------------------------------------------
-- QuizAttempts / QuizResponses — learner history
-- ---------------------------------------------------------------------------
-- This is what the adaptive engine reads. Weak topics live here, not in any vector
-- store: SQL decides WHAT to ask about, retrieval only decides what the source says.
IF OBJECT_ID('dbo.GeneratedQuizAttempts', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.GeneratedQuizAttempts
    (
        attempt_id      NVARCHAR(64) NOT NULL CONSTRAINT PK_GeneratedQuizAttempts PRIMARY KEY,
        learner_id      NVARCHAR(120) NOT NULL,
        started_at      DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
        submitted_at    DATETIME2(3)  NULL,
        score_percent   DECIMAL(5,2)  NULL,
        points_awarded  INT           NULL,
        points_possible INT           NULL,
        passed          BIT           NULL
    );

    CREATE INDEX IX_GeneratedQuizAttempts_Learner
        ON dbo.GeneratedQuizAttempts(learner_id, started_at DESC);
END
GO

IF OBJECT_ID('dbo.GeneratedQuizResponses', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.GeneratedQuizResponses
    (
        response_id    NVARCHAR(64)  NOT NULL CONSTRAINT PK_GeneratedQuizResponses PRIMARY KEY,
        attempt_id     NVARCHAR(64)  NOT NULL,
        learner_id     NVARCHAR(120) NOT NULL,
        question_id    NVARCHAR(64)  NOT NULL,
        topic          NVARCHAR(120) NOT NULL,
        selected       NVARCHAR(500) NULL,   -- comma-separated option_ids
        text_answer    NVARCHAR(300) NULL,
        is_correct     BIT           NOT NULL DEFAULT 0,
        points_awarded INT           NOT NULL DEFAULT 0,
        answered_at    DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT FK_GeneratedQuizResponses_Attempt FOREIGN KEY (attempt_id)
            REFERENCES dbo.GeneratedQuizAttempts(attempt_id) ON DELETE CASCADE,
        CONSTRAINT FK_GeneratedQuizResponses_Question FOREIGN KEY (question_id)
            REFERENCES dbo.GeneratedQuestions(question_id)
    );

    -- The adaptive engine's main read: accuracy per learner per topic.
    CREATE INDEX IX_GeneratedQuizResponses_Mastery
        ON dbo.GeneratedQuizResponses(learner_id, topic) INCLUDE (is_correct);
    CREATE INDEX IX_GeneratedQuizResponses_Question
        ON dbo.GeneratedQuizResponses(question_id) INCLUDE (is_correct);
END
GO

-- ---------------------------------------------------------------------------
-- vw_ServableQuestions — the answer-safe delivery view
-- ---------------------------------------------------------------------------
-- No is_correct column, no answer keys. The endpoint that sends a quiz to a browser
-- MUST read from here: then a careless SELECT * cannot leak the key to the client.
-- Grading joins the underlying tables server-side.
CREATE OR ALTER VIEW dbo.vw_ServableQuestions
AS
SELECT
    q.question_id,
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

-- Per-learner, per-topic accuracy — what weak-topic targeting reads.
CREATE OR ALTER VIEW dbo.vw_LearnerTopicMastery
AS
SELECT
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
GROUP BY r.learner_id, r.topic;
GO
