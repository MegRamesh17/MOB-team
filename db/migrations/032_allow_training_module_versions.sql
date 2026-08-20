-- 032_allow_training_module_versions.sql
--
-- Instructional course generation stages a replacement beside the currently
-- published version. Every generation gets a new module_id.
-- The source-level unique constraint predates that versioning model. It rejects
-- a retry once a prior attempt inserts a module for the same document/topic.

IF
    EXISTS
    (
        SELECT 1
        FROM sys.key_constraints
        WHERE
            parent_object_id = OBJECT_ID('dbo.TrainingModules')
            AND name = 'UQ_TrainingModules_Source'
    )
    ALTER TABLE dbo.trainingmodules
    DROP CONSTRAINT uq_trainingmodules_source;
GO

-- Keep source lookups efficient without preventing multiple staged generations.
IF
    NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE
            object_id = OBJECT_ID('dbo.TrainingModules')
            AND name = 'IX_TrainingModules_Source'
    )
    CREATE INDEX ix_trainingmodules_source
        ON dbo.trainingmodules (company_id, doc_id, topic);
GO
