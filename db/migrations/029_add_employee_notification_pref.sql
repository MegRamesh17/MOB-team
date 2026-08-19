-- 029_add_employee_notification_pref.sql
--
-- Settings: one real, user-controlled preference -- whether this employee wants the
-- reminder/notification emails shared/comms.py already sends (expiry reminders,
-- new-training-assigned notices, manager-triggered nudges). Defaults to on, so nobody
-- silently stops getting compliance-relevant email just because this migration ran.
--
-- Guarded with COL_LENGTH, the same idiom 018 and 028 already use on this table, so
-- this is safe to re-run.

IF COL_LENGTH('dbo.Employees', 'notifications_enabled') IS NULL
    ALTER TABLE dbo.Employees ADD notifications_enabled BIT NOT NULL DEFAULT 1;
GO
