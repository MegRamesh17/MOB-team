-- 018_extend_certificates.sql
--
-- Extends 014_create_certificates.sql so it fits what quizgen actually certifies, and
-- renames one column so the word "Q Score" means one thing.
--
-- 014 is adopted verbatim from the add-certificates branch rather than replaced, so when
-- that branch merges the file is byte-identical and there is nothing to reconcile. Every
-- change it needs is here instead.
--
-- WHAT 014 ASSUMES, AND WHY IT DOES NOT FIT YET
--
--   course_id INT NULL -> Courses(id)
--       quizgen does not certify Courses rows. It certifies a SOURCE DOCUMENT — the same
--       grain trainings and mastery use, so a certificate lines up with the card the
--       learner pressed "start" on. Those documents arrive by upload and have no Courses
--       row. course_id stays for the older Completions-based flow; doc_title is what the
--       quizgen path fills in.
--
--   q_score DECIMAL(5,2)
--       This column holds the score for ONE ATTEMPT. The Q Score a manager reads is a
--       different number at a different grain — per employee, across all their
--       certificates, driven by coverage (docs/q-score.md). Two numbers under one name is
--       how "Q Score 82" becomes ambiguous between "82% compliant" and "scored 82 on one
--       quiz", so the per-attempt one is renamed to what it is.

-- ---------------------------------------------------------------------------
-- What the certificate is FOR
-- ---------------------------------------------------------------------------
IF COL_LENGTH('dbo.Certificates', 'doc_title') IS NULL
    ALTER TABLE dbo.Certificates ADD doc_title NVARCHAR(300) NULL;
GO

-- Behavioural vs technical, so "strong technically, thin on conduct" stays visible
-- instead of being averaged into one number. Defaults to technical rather than being
-- guessed from a title: putting real conduct training in the wrong bucket would skew
-- exactly the split this exists for.
IF COL_LENGTH('dbo.Certificates', 'category') IS NULL
    ALTER TABLE dbo.Certificates ADD category NVARCHAR(20) NOT NULL
        CONSTRAINT DF_Certificates_Category DEFAULT 'technical';
GO

-- ---------------------------------------------------------------------------
-- The rename
-- ---------------------------------------------------------------------------
-- Guarded both ways so this is safe whichever order the branches land in: if
-- add-certificates merges first the column is q_score and gets renamed; if this runs
-- first, or twice, it is already attempt_score and nothing happens.
IF COL_LENGTH('dbo.Certificates', 'q_score') IS NOT NULL
   AND COL_LENGTH('dbo.Certificates', 'attempt_score') IS NULL
    EXEC sp_rename 'dbo.Certificates.q_score', 'attempt_score', 'COLUMN';
GO

-- A learner can hold several certificates for one training — a retake issues a new one
-- and the old stays as history, which is what makes best-score-of-record possible and
-- leaves an audit trail. So this index is deliberately NOT unique.
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_Certificates_Employee_Doc'
                 AND object_id = OBJECT_ID('dbo.Certificates'))
    CREATE INDEX IX_Certificates_Employee_Doc
        ON dbo.Certificates(employee_id, doc_title);
GO
