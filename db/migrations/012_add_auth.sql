-- 012_add_auth.sql
-- Track A (self-hosted email/password auth — Entra pivot, permission-blocked for now).
--
-- Deliberately does NOT create a new Users table. Employees already has email
-- (NOT NULL UNIQUE, from 001_create_employees.sql), company_id (from
-- 009_add_multitenancy.sql), and role_id -> Roles.access_role (from
-- 007_alter_employees_add_role.sql). Adding a separate identity table would mean two
-- records of "who this person is" that have to be kept in sync forever — password_hash
-- belongs on the row that's already the source of truth.
--
-- Nullable for the same reason 007's role_id was added nullable first: existing seeded
-- employees don't have a password yet. Backfill via a one-off admin script (or an
-- interactive "set your password" first-login flow), then tighten to NOT NULL once
-- every real account has one — don't do that until you've confirmed nothing's left null.

ALTER TABLE Employees ADD password_hash NVARCHAR(255) NULL;
