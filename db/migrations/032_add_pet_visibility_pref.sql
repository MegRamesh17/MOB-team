-- 032_add_pet_visibility_pref.sql
--
-- Settings: whether this employee wants the floating desk pet shown at all. Some
-- people find a moving character in the corner distracting -- especially while taking
-- a quiz -- and want it gone everywhere on the site, not just during quizzes.
-- Defaults to on, so nobody loses the pet just because this migration ran.
--
-- Guarded with COL_LENGTH, the same idiom 018, 028 and 029 already use on this table,
-- so this is safe to re-run.

IF COL_LENGTH('dbo.Employees', 'pet_visible') IS NULL
    ALTER TABLE dbo.Employees ADD pet_visible BIT NOT NULL DEFAULT 1;
GO
