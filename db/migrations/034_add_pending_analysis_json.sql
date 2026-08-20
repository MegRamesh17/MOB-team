-- 034_add_pending_analysis_json.sql
--
-- Adds the column 033_create_training_documents.sql SHOULD have created if it had run
-- fresh everywhere, but didn't: dbo.TrainingDocuments already existed in this database
-- from an earlier deploy of a 7-column version of that same migration file, so 033's
-- `IF OBJECT_ID(...) IS NULL` guard skipped the whole CREATE TABLE block on this
-- database -- the guard correctly protects against re-creating an existing table, but
-- has no way to notice the existing table is missing a column a later edit of the same
-- file added. Migrations here are tracked by filename in dbo.SchemaMigrations, not by
-- content, so editing 033 after it was already recorded as applied here would never
-- re-run it. A new, purely additive migration is the only way to actually land this
-- column on a database that already has the table.

IF COL_LENGTH('dbo.TrainingDocuments', 'pending_analysis_json') IS NULL
    ALTER TABLE dbo.TrainingDocuments ADD pending_analysis_json NVARCHAR(MAX) NULL;
GO
