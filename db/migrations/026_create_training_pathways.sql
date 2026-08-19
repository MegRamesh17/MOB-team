-- 026_create_training_pathways.sql
--
-- Turns source-document sections into an ordered learner pathway:
-- diagnostic -> required module lessons/checkpoints -> final certification assessment.
-- Every statement is idempotent because migrations may be retried by GitHub Actions.

IF OBJECT_ID('dbo.TrainingModules', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.TrainingModules
    (
        module_id     NVARCHAR(64)  NOT NULL CONSTRAINT PK_TrainingModules PRIMARY KEY,
        company_id    INT           NOT NULL,
        doc_id        NVARCHAR(64)  NOT NULL,
        doc_title     NVARCHAR(300) NOT NULL,
        topic         NVARCHAR(120) NOT NULL,
        heading       NVARCHAR(300) NOT NULL,
        source_order  INT           NOT NULL,
        role_scope    NVARCHAR(200) NOT NULL DEFAULT 'ALL',
        created_at    DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT FK_TrainingModules_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id),
        CONSTRAINT UQ_TrainingModules_Source UNIQUE (company_id, doc_id, topic)
    );

    CREATE INDEX IX_TrainingModules_Training
        ON dbo.TrainingModules(company_id, doc_title, source_order);
END
GO

IF OBJECT_ID('dbo.EmployeeTrainingProgress', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.EmployeeTrainingProgress
    (
        company_id             INT            NOT NULL,
        learner_id             NVARCHAR(120)  NOT NULL,
        doc_id                 NVARCHAR(64)   NOT NULL,
        doc_title              NVARCHAR(300)  NOT NULL,
        diagnostic_attempt_id  NVARCHAR(64)   NULL,
        diagnostic_completed_at DATETIME2(3)  NULL,
        diagnostic_scores_json NVARCHAR(MAX)  NULL,
        pathway_json           NVARCHAR(MAX)  NULL,
        updated_at             DATETIME2(3)   NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_EmployeeTrainingProgress PRIMARY KEY
            (company_id, learner_id, doc_id),
        CONSTRAINT FK_EmployeeTrainingProgress_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id),
        CONSTRAINT CK_TrainingProgress_ScoresJson CHECK
            (diagnostic_scores_json IS NULL OR ISJSON(diagnostic_scores_json) = 1),
        CONSTRAINT CK_TrainingProgress_PathwayJson CHECK
            (pathway_json IS NULL OR ISJSON(pathway_json) = 1)
    );
END
GO

IF OBJECT_ID('dbo.EmployeeModuleProgress', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.EmployeeModuleProgress
    (
        company_id       INT            NOT NULL,
        learner_id       NVARCHAR(120)  NOT NULL,
        module_id        NVARCHAR(64)   NOT NULL,
        status           NVARCHAR(20)   NOT NULL DEFAULT 'available',
        best_score       DECIMAL(5,2)   NOT NULL DEFAULT 0,
        attempt_count    INT            NOT NULL DEFAULT 0,
        weak_sections_json NVARCHAR(MAX) NULL,
        completed_at     DATETIME2(3)   NULL,
        updated_at       DATETIME2(3)   NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_EmployeeModuleProgress PRIMARY KEY
            (company_id, learner_id, module_id),
        CONSTRAINT FK_EmployeeModuleProgress_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id),
        CONSTRAINT FK_EmployeeModuleProgress_Module FOREIGN KEY (module_id)
            REFERENCES dbo.TrainingModules(module_id),
        CONSTRAINT CK_EmployeeModuleProgress_Status CHECK
            (status IN ('available', 'in-progress', 'needs-review', 'passed')),
        CONSTRAINT CK_EmployeeModuleProgress_WeakJson CHECK
            (weak_sections_json IS NULL OR ISJSON(weak_sections_json) = 1)
    );
END
GO

IF COL_LENGTH('dbo.GeneratedQuizAttempts', 'training_doc_id') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttempts ADD training_doc_id NVARCHAR(64) NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuizAttempts', 'training_title') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttempts ADD training_title NVARCHAR(300) NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuizAttempts', 'module_id') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttempts ADD module_id NVARCHAR(64) NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuizAttempts', 'attempt_kind') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttempts ADD attempt_kind NVARCHAR(20) NOT NULL
        CONSTRAINT DF_QuizAttempts_Kind DEFAULT 'legacy';
GO
IF COL_LENGTH('dbo.GeneratedQuizAttempts', 'question_target') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttempts ADD question_target INT NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuizAttempts', 'passing_score') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttempts ADD passing_score DECIMAL(5,2) NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuizAttempts', 'current_difficulty') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttempts ADD current_difficulty NVARCHAR(20) NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuizAttempts', 'blueprint_json') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttempts ADD blueprint_json NVARCHAR(MAX) NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_QuizAttempts_Kind')
    ALTER TABLE dbo.GeneratedQuizAttempts ADD CONSTRAINT CK_QuizAttempts_Kind CHECK
        (attempt_kind IN ('legacy', 'diagnostic', 'module', 'final'));
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_QuizAttempts_Module')
    ALTER TABLE dbo.GeneratedQuizAttempts ADD CONSTRAINT FK_QuizAttempts_Module
        FOREIGN KEY (module_id) REFERENCES dbo.TrainingModules(module_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QuizAttempts_Pathway')
    CREATE INDEX IX_QuizAttempts_Pathway
        ON dbo.GeneratedQuizAttempts(company_id, learner_id, training_doc_id, attempt_kind);
GO

IF COL_LENGTH('dbo.GeneratedQuizAttemptQuestions', 'purpose') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttemptQuestions ADD purpose NVARCHAR(20) NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuizAttemptQuestions', 'selected') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttemptQuestions ADD selected NVARCHAR(500) NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuizAttemptQuestions', 'text_answer') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttemptQuestions ADD text_answer NVARCHAR(MAX) NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuizAttemptQuestions', 'is_correct') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttemptQuestions ADD is_correct BIT NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuizAttemptQuestions', 'answered_at') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttemptQuestions ADD answered_at DATETIME2(3) NULL;
GO

-- Backfill modules for documents uploaded before pathway support. The Function also
-- MERGEs modules when it serves a pathway, covering later uploads without depending on
-- a migration rerun.
;WITH source_modules AS
(
    SELECT company_id, doc_id, doc_title, topic,
           MIN(section) AS heading,
           MIN(page_start) AS source_order,
           MIN(role_scope) AS role_scope
      FROM dbo.SourceChunks
     GROUP BY company_id, doc_id, doc_title, topic
)
INSERT INTO dbo.TrainingModules
    (module_id, company_id, doc_id, doc_title, topic, heading, source_order, role_scope)
SELECT
    'mod_' + LEFT(CONVERT(VARCHAR(64), HASHBYTES(
        'SHA2_256', CONCAT(company_id, '|', doc_id, '|', topic)), 2), 24),
    company_id, doc_id, doc_title, topic, heading, source_order, role_scope
FROM source_modules AS source
WHERE NOT EXISTS
(
    SELECT 1 FROM dbo.TrainingModules AS target
     WHERE target.company_id = source.company_id
       AND target.doc_id = source.doc_id
       AND target.topic = source.topic
);
GO
