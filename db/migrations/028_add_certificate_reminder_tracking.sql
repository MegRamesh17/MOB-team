-- 028_add_certificate_reminder_tracking.sql
--
-- Track E: the daily expiry-reminder timer function's "don't send this twice" flag.
--
-- dbo.Completions already has a reminder_sent_at column (004_create_completions.sql),
-- but nothing writes to Completions any more -- POST /quiz/submit issues certificates
-- into dbo.Certificates (018_extend_certificates.sql), which never got the same column.
-- A reminder job built against Completions would run daily against a table nothing
-- populates, and find nothing.
--
-- Guarded with COL_LENGTH, the same idiom 018 already uses on this table, so this is
-- safe to re-run.

IF COL_LENGTH('dbo.Certificates', 'reminder_sent_at') IS NULL
    ALTER TABLE dbo.Certificates ADD reminder_sent_at DATETIME2 NULL;
GO
