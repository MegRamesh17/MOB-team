-- 027_add_guarded_ai_grading.sql
-- Locked rubrics and pre-authored fallback questions for short, prompt and Python answers.

IF COL_LENGTH('dbo.GeneratedQuestions', 'rubric_json') IS NULL
    ALTER TABLE dbo.GeneratedQuestions ADD rubric_json NVARCHAR(MAX) NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuestions', 'fallback_json') IS NULL
    ALTER TABLE dbo.GeneratedQuestions ADD fallback_json NVARCHAR(MAX) NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuestions', 'grading_version') IS NULL
    ALTER TABLE dbo.GeneratedQuestions ADD grading_version NVARCHAR(40) NULL;
GO

IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_GeneratedQuestions_Type')
    ALTER TABLE dbo.GeneratedQuestions DROP CONSTRAINT CK_GeneratedQuestions_Type;
GO
ALTER TABLE dbo.GeneratedQuestions ADD CONSTRAINT CK_GeneratedQuestions_Type CHECK
    (question_type IN
        ('MultipleChoice', 'MultiSelect', 'TrueFalse', 'FillInBlank',
         'ShortAnswer', 'PromptResponse', 'PythonCode'));
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_GeneratedQuestions_RubricJson')
    ALTER TABLE dbo.GeneratedQuestions ADD CONSTRAINT CK_GeneratedQuestions_RubricJson CHECK
        (rubric_json IS NULL OR ISJSON(rubric_json) = 1);
GO
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_GeneratedQuestions_FallbackJson')
    ALTER TABLE dbo.GeneratedQuestions ADD CONSTRAINT CK_GeneratedQuestions_FallbackJson CHECK
        (fallback_json IS NULL OR ISJSON(fallback_json) = 1);
GO

IF COL_LENGTH('dbo.GeneratedQuizAttemptQuestions', 'fallback_active') IS NULL
    ALTER TABLE dbo.GeneratedQuizAttemptQuestions ADD fallback_active BIT NOT NULL
        CONSTRAINT DF_AttemptQuestions_FallbackActive DEFAULT 0;
GO

IF OBJECT_ID('dbo.GeneratedGradingEvents', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.GeneratedGradingEvents
    (
        grading_event_id NVARCHAR(64)   NOT NULL CONSTRAINT PK_GeneratedGradingEvents PRIMARY KEY,
        company_id       INT            NOT NULL,
        attempt_id       NVARCHAR(64)   NOT NULL,
        question_id      NVARCHAR(64)   NOT NULL,
        verdict          NVARCHAR(20)   NOT NULL,
        rubric_score     DECIMAL(5,2)   NULL,
        confidence       DECIMAL(5,4)   NULL,
        reason           NVARCHAR(500)  NULL,
        criteria_json    NVARCHAR(MAX)  NULL,
        grader_model     NVARCHAR(100)  NULL,
        grading_version  NVARCHAR(40)   NOT NULL,
        fallback_used    BIT            NOT NULL DEFAULT 0,
        created_at       DATETIME2(3)   NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT FK_GradingEvents_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id),
        CONSTRAINT FK_GradingEvents_Attempt FOREIGN KEY (attempt_id)
            REFERENCES dbo.GeneratedQuizAttempts(attempt_id),
        CONSTRAINT FK_GradingEvents_Question FOREIGN KEY (question_id)
            REFERENCES dbo.GeneratedQuestions(question_id),
        CONSTRAINT CK_GradingEvents_Verdict CHECK
            (verdict IN ('correct', 'incorrect', 'uncertain', 'system_error')),
        CONSTRAINT CK_GradingEvents_CriteriaJson CHECK
            (criteria_json IS NULL OR ISJSON(criteria_json) = 1)
    );

    CREATE INDEX IX_GradingEvents_Attempt
        ON dbo.GeneratedGradingEvents(company_id, attempt_id, created_at);
END
GO
