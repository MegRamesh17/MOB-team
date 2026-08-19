-- verify_schema.sql
--
-- Checks what is ACTUALLY in the database against what the migrations should have built.
--
-- WHY THIS EXISTS
-- dbo.SchemaMigrations recorded 009 as applied when dbo.Companies had never been created.
-- The runner marked every file done whether sqlcmd succeeded or not, so the table that
-- says "what has been applied" drifted from the database it was describing. That is fixed
-- in migrate-database.yml, but the fix only stops it happening again — it does not tell
-- anyone what is missing NOW, and the same commit notes 011 went the same way.
--
-- 021 repairs Companies specifically. This asks the broader question: of everything the
-- migrations claim to have created, what is not there?
--
-- Reports EVERYTHING missing in one pass rather than failing at the first gap. Finding
-- one missing table, repairing it, re-running and finding the next is how a morning
-- disappears — and each round trip needs a firewall rule and a workflow run.
--
-- Safe to run against any database, changes nothing. RAISERROR at the end so a CI step
-- fails when something is missing.

SET NOCOUNT ON;

DECLARE @missing TABLE (
    kind     NVARCHAR(20),
    name     NVARCHAR(200),
    added_by NVARCHAR(60)
);

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------
INSERT INTO @missing (kind, name, added_by)
SELECT 'table', v.name, v.added_by
FROM (VALUES
    ('Employees',                      '001'),
    ('Courses',                        '002'),
    ('QuizQuestions',                  '003'),
    ('Completions',                    '004'),
    ('QuizAttempts',                   '005'),
    ('Departments',                    '006'),
    ('Teams',                          '006'),
    ('Roles',                          '006'),
    ('CourseRoles',                    '008'),
    -- 009 is the one that silently did not land. 021 repairs it.
    ('Companies',                      '009 / repaired by 021'),
    ('SourceChunks',                   '011'),
    ('GeneratedQuestions',             '011'),
    ('GeneratedOptions',               '011'),
    ('GeneratedAnswerKeys',            '011'),
    ('GeneratedQuizAttempts',          '011'),
    ('GeneratedQuizResponses',         '011'),
    ('Certificates',                   '014'),
    ('GeneratedQuizAttemptQuestions',  '017'),
    ('RoleRequirements',               '019'),
    ('QuizgenRoles',                   '025'),
    ('GenerationJobs',                 '025'),
    ('TrainingModules',                '026'),
    ('EmployeeTrainingProgress',       '026'),
    ('EmployeeModuleProgress',         '026'),
    ('GeneratedGradingEvents',         '027'),
    ('TrainingModuleRoles',            '030'),
    ('ModuleLearningPoints',           '030'),
    ('LessonPages',                    '030'),
    ('EmployeeLessonPageProgress',     '030'),
    ('SchemaMigrations',               'migrate-database.yml')
) AS v(name, added_by)
WHERE OBJECT_ID('dbo.' + v.name, 'U') IS NULL;

-- ---------------------------------------------------------------------------
-- Columns added by later migrations
-- ---------------------------------------------------------------------------
-- These are the ones an ALTER added, so a table can exist while the column that makes it
-- useful does not. company_id is the whole tenant boundary; role_code is what makes
-- role-scoped training possible; password_hash is what makes sign-in possible.
INSERT INTO @missing (kind, name, added_by)
SELECT 'column', v.tbl + '.' + v.col, v.added_by
FROM (VALUES
    ('Employees',              'role_id',        '007'),
    ('Employees',              'password_hash',  '012'),
    ('Departments',            'company_id',     '009'),
    ('Employees',              'company_id',     '009'),
    ('Courses',                'company_id',     '009'),
    ('Roles',                  'role_code',      '016'),
    ('Certificates',           'doc_title',      '018'),
    ('Certificates',           'category',       '018'),
    ('Certificates',           'attempt_score',  '018 (renamed from q_score)'),
    ('SourceChunks',           'company_id',     '020'),
    ('GeneratedQuestions',     'company_id',     '020'),
    ('GeneratedQuizAttempts',  'company_id',     '020'),
    ('GeneratedQuizResponses', 'company_id',     '020'),
    ('Certificates',           'company_id',     '020'),
    ('GeneratedQuizAttempts',  'training_doc_id','026'),
    ('GeneratedQuizAttempts',  'training_title', '026'),
    ('GeneratedQuizAttempts',  'module_id',      '026'),
    ('GeneratedQuizAttempts',  'attempt_kind',   '026'),
    ('GeneratedQuizAttempts',  'question_target','026'),
    ('GeneratedQuizAttempts',  'passing_score',  '026'),
    ('GeneratedQuizAttemptQuestions', 'purpose',    '026'),
    ('GeneratedQuizAttemptQuestions', 'is_correct', '026'),
    ('GeneratedQuizAttemptQuestions', 'answered_at','026'),
    ('GeneratedQuestions',     'rubric_json',      '027'),
    ('GeneratedQuestions',     'fallback_json',    '027'),
    ('GeneratedQuestions',     'grading_version',  '027'),
    ('GeneratedQuizAttemptQuestions', 'fallback_active', '027'),
    ('TrainingModules',       'status',               '030'),
    ('TrainingModules',       'active_generation_id', '030'),
    ('TrainingModules',       'lesson_word_count',    '030'),
    ('TrainingModules',       'learning_point_count', '030'),
    ('GeneratedQuestions',    'module_id',            '030'),
    ('GeneratedQuestions',    'lesson_page_id',       '030'),
    ('GeneratedQuestions',    'learning_point_id',    '030')
) AS v(tbl, col, added_by)
WHERE OBJECT_ID('dbo.' + v.tbl, 'U') IS NOT NULL     -- the table itself is reported above
  AND COL_LENGTH('dbo.' + v.tbl, v.col) IS NULL;

-- Free-text pathway answers can exceed the original 300-character legacy limit.
INSERT INTO @missing (kind, name, added_by)
SELECT 'column width', 'GeneratedQuizResponses.text_answer', '028'
WHERE OBJECT_ID('dbo.GeneratedQuizResponses', 'U') IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM sys.columns
      WHERE object_id = OBJECT_ID('dbo.GeneratedQuizResponses')
        AND name = 'text_answer' AND max_length <> -1);

-- ---------------------------------------------------------------------------
-- Views
-- ---------------------------------------------------------------------------
-- Easy to miss, and they fail differently: a view is a stored query, so a stale one keeps
-- returning rows. vw_LearnerTopicMastery without company_id merges two companies'
-- answers however carefully its callers are scoped.
INSERT INTO @missing (kind, name, added_by)
SELECT 'view', v.name, v.added_by
FROM (VALUES
    ('vw_ServableQuestions',   '011, updated by 020'),
    ('vw_LearnerTopicMastery', '011, updated by 020')
) AS v(name, added_by)
WHERE OBJECT_ID('dbo.' + v.name, 'V') IS NULL;

INSERT INTO @missing (kind, name, added_by)
SELECT 'view column', 'vw_LearnerTopicMastery.company_id', '020'
WHERE OBJECT_ID('dbo.vw_LearnerTopicMastery', 'V') IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM sys.columns
      WHERE object_id = OBJECT_ID('dbo.vw_LearnerTopicMastery') AND name = 'company_id');

-- ---------------------------------------------------------------------------
-- Report
-- ---------------------------------------------------------------------------
PRINT '--- what the database actually contains ---';

SELECT filename AS applied_migration FROM dbo.SchemaMigrations ORDER BY filename;

SELECT
    (SELECT COUNT(*) FROM dbo.Companies)   AS companies,
    (SELECT COUNT(*) FROM dbo.Employees)   AS employees,
    (SELECT COUNT(*) FROM dbo.Employees WHERE password_hash IS NOT NULL) AS can_sign_in,
    (SELECT COUNT(*) FROM dbo.Roles)       AS roles,
    (SELECT COUNT(*) FROM dbo.Roles WHERE role_code IS NOT NULL) AS roles_mapped;

IF EXISTS (SELECT 1 FROM @missing)
BEGIN
    PRINT '';
    PRINT '--- MISSING ---';
    SELECT kind, name, added_by FROM @missing ORDER BY kind, name;

    DECLARE @count INT = (SELECT COUNT(*) FROM @missing);
    DECLARE @message NVARCHAR(400) =
        CAST(@count AS NVARCHAR(10)) +
        ' schema object(s) missing. SchemaMigrations may say they were applied — it has ' +
        'been wrong before. Re-run migrate-database after repairing, and check this again.';
    RAISERROR(@message, 16, 1);
END
ELSE
BEGIN
    PRINT '';
    PRINT 'Schema matches what the migrations should have built. Nothing missing.';
END
GO
