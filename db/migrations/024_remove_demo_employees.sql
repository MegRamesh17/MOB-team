-- 024_remove_demo_employees.sql
--
-- Removes the eight @demo.com placeholder people and everything hanging off them.
--
-- WHY
-- seed_data.sql invented Dana Whitfield, Ethan Brooks and six others so there was
-- something to look at before real data existed. Real employees are now loaded, and the
-- two sets sit in the same tables -- so every headcount, every Q Score rollup and every
-- org chart walk mixes invented people in with real ones. That is worse than having no
-- demo data at all, because the numbers look plausible.
--
-- The matching people are also removed from seed_data.sql, so re-seeding does not put
-- them back. This migration handles rows already in the database, which the seed change
-- cannot reach.
--
-- SCOPE
-- Matched strictly on '@demo.com', which no real address uses. Nothing here touches a
-- row that is not keyed to one of those addresses. Courses and quiz questions are NOT
-- removed -- they are placeholder content rather than demo people, they carry no
-- @demo.com key, and dropping them would take real completion history with them.
--
-- ORDER MATTERS
--   1. Certificates has a plain FK with no ON DELETE CASCADE, so its rows go first or
--      the delete below fails on a constraint violation.
--   2. Employees.manager_id points at Employees, so anyone still reporting to a demo
--      person has to be detached first. Real employees should never be in this state --
--      but if a real person was parented to a demo manager by mistake, NULLing it keeps
--      the delete working and leaves them visibly unassigned rather than deleted.
--   3. Completions and QuizAttempts cascade on their own (004, 005).
--   4. GeneratedQuiz* keys learners by email string, not by FK, so nothing cascades and
--      orphans would survive as rows nobody can attribute. Cleared explicitly.
--
-- Safe to re-run: every statement is a filtered DELETE or UPDATE that matches nothing
-- once it has run.

SET NOCOUNT ON;

DECLARE @demo TABLE (id INT PRIMARY KEY, email NVARCHAR(255));

INSERT INTO @demo (id, email)
SELECT id, email FROM dbo.Employees WHERE email LIKE '%@demo.com';

DECLARE @found INT = (SELECT COUNT(*) FROM @demo);
PRINT '024: found ' + CAST(@found AS VARCHAR) + ' @demo.com employee(s).';

-- 1. Certificates — no cascade, so these must go first.
DELETE c FROM dbo.Certificates c JOIN @demo d ON d.id = c.employee_id;
PRINT '024: removed ' + CAST(@@ROWCOUNT AS VARCHAR) + ' certificate(s).';

-- 2. Detach anyone still reporting to a demo person, so the delete cannot fail on the
--    self-referencing FK. Expected to be zero once the demo people are the only ones
--    pointing at each other.
UPDATE e SET manager_id = NULL
  FROM dbo.Employees e
  JOIN @demo d ON d.id = e.manager_id
 WHERE e.email NOT LIKE '%@demo.com';
PRINT '024: detached ' + CAST(@@ROWCOUNT AS VARCHAR) + ' real employee(s) from a demo manager.';

-- 3. Learner history in the generated-quiz tables, keyed by email rather than by FK.
DELETE r FROM dbo.GeneratedQuizResponses r JOIN @demo d ON d.email = r.learner_id;
DELETE a FROM dbo.GeneratedQuizAttempts  a JOIN @demo d ON d.email = a.learner_id;

-- 4. The people. Completions and QuizAttempts cascade from here.
DELETE e FROM dbo.Employees e JOIN @demo d ON d.id = e.id;
PRINT '024: removed ' + CAST(@@ROWCOUNT AS VARCHAR) + ' employee(s).';

DECLARE @remaining INT = (SELECT COUNT(*) FROM dbo.Employees WHERE email LIKE '%@demo.com');
IF @remaining > 0
    RAISERROR('024: %d @demo.com employee(s) still present after cleanup.', 16, 1, @remaining);
GO
