-- 030_create_instructional_courses.sql
--
-- Persist the instructional layer between source ingestion and assessment generation.
-- A source chunk is evidence, not a lesson; a topic is a grouping hint, not a module.
-- This migration gives generated courses ordered lesson pages, explicit learning points,
-- normalized multi-role audiences, and page-level learner progress.

IF COL_LENGTH('dbo.TrainingModules', 'status') IS NULL
    ALTER TABLE dbo.TrainingModules ADD status NVARCHAR(20) NOT NULL
        CONSTRAINT DF_TrainingModules_Status DEFAULT 'ready';
GO
IF COL_LENGTH('dbo.TrainingModules', 'summary') IS NULL
    ALTER TABLE dbo.TrainingModules ADD summary NVARCHAR(1000) NULL;
GO
IF COL_LENGTH('dbo.TrainingModules', 'lesson_word_count') IS NULL
    ALTER TABLE dbo.TrainingModules ADD lesson_word_count INT NOT NULL
        CONSTRAINT DF_TrainingModules_LessonWords DEFAULT 0;
GO
IF COL_LENGTH('dbo.TrainingModules', 'learning_point_count') IS NULL
    ALTER TABLE dbo.TrainingModules ADD learning_point_count INT NOT NULL
        CONSTRAINT DF_TrainingModules_LearningPoints DEFAULT 0;
GO
IF COL_LENGTH('dbo.TrainingModules', 'generation_version') IS NULL
    ALTER TABLE dbo.TrainingModules ADD generation_version NVARCHAR(40) NULL;
GO
IF COL_LENGTH('dbo.TrainingModules', 'active_generation_id') IS NULL
    ALTER TABLE dbo.TrainingModules ADD active_generation_id NVARCHAR(64) NULL;
GO
IF COL_LENGTH('dbo.TrainingModules', 'quality_notes_json') IS NULL
    ALTER TABLE dbo.TrainingModules ADD quality_notes_json NVARCHAR(MAX) NULL;
GO
IF COL_LENGTH('dbo.TrainingModules', 'updated_at') IS NULL
    ALTER TABLE dbo.TrainingModules ADD updated_at DATETIME2(3) NOT NULL
        CONSTRAINT DF_TrainingModules_Updated DEFAULT SYSUTCDATETIME();
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_TrainingModules_Status')
    ALTER TABLE dbo.TrainingModules ADD CONSTRAINT CK_TrainingModules_Status CHECK
        (status IN ('draft', 'ready', 'insufficient', 'retired'));
GO
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_TrainingModules_QualityJson')
    ALTER TABLE dbo.TrainingModules ADD CONSTRAINT CK_TrainingModules_QualityJson CHECK
        (quality_notes_json IS NULL OR ISJSON(quality_notes_json) = 1);
GO

IF OBJECT_ID('dbo.TrainingModuleRoles', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.TrainingModuleRoles
    (
        company_id INT          NOT NULL,
        module_id  NVARCHAR(64) NOT NULL,
        role_code  NVARCHAR(40) NOT NULL,
        created_at DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_TrainingModuleRoles PRIMARY KEY (company_id, module_id, role_code),
        CONSTRAINT FK_TrainingModuleRoles_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id),
        CONSTRAINT FK_TrainingModuleRoles_Module FOREIGN KEY (module_id)
            REFERENCES dbo.TrainingModules(module_id) ON DELETE CASCADE
    );

    CREATE INDEX IX_TrainingModuleRoles_Role
        ON dbo.TrainingModuleRoles(company_id, role_code, module_id);
END
GO

-- Preserve every existing single-role assignment. New courses write one row per role.
INSERT INTO dbo.TrainingModuleRoles (company_id, module_id, role_code)
SELECT company_id, module_id, COALESCE(NULLIF(role_scope, ''), 'ALL')
  FROM dbo.TrainingModules AS module
 WHERE NOT EXISTS
 (
     SELECT 1 FROM dbo.TrainingModuleRoles AS audience
      WHERE audience.company_id = module.company_id
        AND audience.module_id = module.module_id
 );
GO

IF OBJECT_ID('dbo.ModuleLearningPoints', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ModuleLearningPoints
    (
        learning_point_id NVARCHAR(64)   NOT NULL CONSTRAINT PK_ModuleLearningPoints PRIMARY KEY,
        company_id        INT            NOT NULL,
        module_id         NVARCHAR(64)   NOT NULL,
        generation_id     NVARCHAR(64)   NOT NULL,
        point_order       INT            NOT NULL,
        statement         NVARCHAR(1000) NOT NULL,
        evidence_json     NVARCHAR(MAX)  NOT NULL,
        created_at        DATETIME2(3)   NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT FK_ModuleLearningPoints_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id),
        CONSTRAINT FK_ModuleLearningPoints_Module FOREIGN KEY (module_id)
            REFERENCES dbo.TrainingModules(module_id) ON DELETE CASCADE,
        CONSTRAINT UQ_ModuleLearningPoints_Order UNIQUE
            (company_id, module_id, generation_id, point_order),
        CONSTRAINT CK_ModuleLearningPoints_EvidenceJson CHECK (ISJSON(evidence_json) = 1)
    );

    CREATE INDEX IX_ModuleLearningPoints_Module
        ON dbo.ModuleLearningPoints(company_id, module_id, point_order);
END
GO

IF OBJECT_ID('dbo.LessonPages', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.LessonPages
    (
        page_id                 NVARCHAR(64)  NOT NULL CONSTRAINT PK_LessonPages PRIMARY KEY,
        company_id              INT           NOT NULL,
        module_id               NVARCHAR(64)  NOT NULL,
        generation_id           NVARCHAR(64)  NOT NULL,
        page_order              INT           NOT NULL,
        title                   NVARCHAR(300) NOT NULL,
        page_type               NVARCHAR(30)  NOT NULL,
        body                    NVARCHAR(MAX) NOT NULL,
        word_count              INT           NOT NULL,
        learning_point_ids_json NVARCHAR(MAX) NOT NULL,
        citations_json          NVARCHAR(MAX) NOT NULL,
        created_at              DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
        updated_at              DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT FK_LessonPages_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id),
        CONSTRAINT FK_LessonPages_Module FOREIGN KEY (module_id)
            REFERENCES dbo.TrainingModules(module_id) ON DELETE CASCADE,
        CONSTRAINT UQ_LessonPages_Order UNIQUE
            (company_id, module_id, generation_id, page_order),
        CONSTRAINT CK_LessonPages_Type CHECK
            (page_type IN ('concept', 'worked-example', 'practice', 'common-mistakes', 'recap')),
        CONSTRAINT CK_LessonPages_LearningJson CHECK (ISJSON(learning_point_ids_json) = 1),
        CONSTRAINT CK_LessonPages_CitationsJson CHECK (ISJSON(citations_json) = 1),
        CONSTRAINT CK_LessonPages_WordCount CHECK (word_count >= 0)
    );

    CREATE INDEX IX_LessonPages_Module
        ON dbo.LessonPages(company_id, module_id, page_order);
END
GO

IF OBJECT_ID('dbo.EmployeeLessonPageProgress', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.EmployeeLessonPageProgress
    (
        company_id  INT            NOT NULL,
        learner_id  NVARCHAR(120)  NOT NULL,
        page_id     NVARCHAR(64)   NOT NULL,
        completed_at DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_EmployeeLessonPageProgress PRIMARY KEY
            (company_id, learner_id, page_id),
        CONSTRAINT FK_EmployeeLessonPageProgress_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id),
        CONSTRAINT FK_EmployeeLessonPageProgress_Page FOREIGN KEY (page_id)
            REFERENCES dbo.LessonPages(page_id) ON DELETE CASCADE
    );
END
GO

IF COL_LENGTH('dbo.GeneratedQuestions', 'module_id') IS NULL
    ALTER TABLE dbo.GeneratedQuestions ADD module_id NVARCHAR(64) NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuestions', 'lesson_page_id') IS NULL
    ALTER TABLE dbo.GeneratedQuestions ADD lesson_page_id NVARCHAR(64) NULL;
GO
IF COL_LENGTH('dbo.GeneratedQuestions', 'learning_point_id') IS NULL
    ALTER TABLE dbo.GeneratedQuestions ADD learning_point_id NVARCHAR(64) NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_GeneratedQuestions_Module')
    ALTER TABLE dbo.GeneratedQuestions ADD CONSTRAINT FK_GeneratedQuestions_Module
        FOREIGN KEY (module_id) REFERENCES dbo.TrainingModules(module_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_GeneratedQuestions_LessonPage')
    ALTER TABLE dbo.GeneratedQuestions ADD CONSTRAINT FK_GeneratedQuestions_LessonPage
        FOREIGN KEY (lesson_page_id) REFERENCES dbo.LessonPages(page_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_GeneratedQuestions_LearningPoint')
    ALTER TABLE dbo.GeneratedQuestions ADD CONSTRAINT FK_GeneratedQuestions_LearningPoint
        FOREIGN KEY (learning_point_id) REFERENCES dbo.ModuleLearningPoints(learning_point_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_GeneratedQuestions_Module')
    CREATE INDEX IX_GeneratedQuestions_Module
        ON dbo.GeneratedQuestions(company_id, module_id, review_status, difficulty);
GO

-- Existing questions keep working and become addressable through the normalized module.
UPDATE question
   SET module_id = module.module_id
  FROM dbo.GeneratedQuestions AS question
  JOIN dbo.SourceChunks AS chunk
    ON chunk.company_id = question.company_id
   AND chunk.chunk_id = question.source_chunk_id
  JOIN dbo.TrainingModules AS module
    ON module.company_id = chunk.company_id
   AND module.doc_id = chunk.doc_id
   AND module.topic = chunk.topic
 WHERE question.module_id IS NULL;
GO
