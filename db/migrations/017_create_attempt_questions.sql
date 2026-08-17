-- 017_create_attempt_questions.sql
--
-- Records which questions were served in which attempt.
--
-- WHY THIS IS A SECURITY REQUIREMENT, NOT BOOKKEEPING
--
-- POST /quiz/answer grades one question mid-quiz so the UI can show immediate feedback
-- without the answer key ever reaching the browser. To do that it has to reveal the key
-- for that one question — which means something must stop a caller asking it about any
-- question in the bank. Without that check the endpoint is an oracle: loop over
-- question_ids, collect every correct answer, then take the quiz.
--
-- scripts/devserver.py holds the served list in a module-level dict (`_IN_FLIGHT`). That
-- works for one process on a laptop and cannot work here: the Function App runs multiple
-- instances behind a load balancer and restarts freely, so an attempt started on one
-- instance is unknown to the next. The list has to be in the database.
--
-- GeneratedQuizAttempts already records that an attempt happened and who by; it does not
-- record what was in it. This adds that.
--
-- Also makes submit verifiable: a submission can be checked against what was actually
-- served, rather than trusting the client's list of question ids.

IF OBJECT_ID('dbo.GeneratedQuizAttemptQuestions', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.GeneratedQuizAttemptQuestions
    (
        attempt_id  NVARCHAR(64) NOT NULL,
        question_id NVARCHAR(64) NOT NULL,
        -- The order the question was served in, so a resumed quiz can be re-rendered
        -- in the same sequence rather than reshuffled under the learner.
        sort_order  INT          NOT NULL DEFAULT 0,

        CONSTRAINT PK_GeneratedQuizAttemptQuestions PRIMARY KEY (attempt_id, question_id),
        CONSTRAINT FK_AttemptQuestions_Attempt FOREIGN KEY (attempt_id)
            REFERENCES dbo.GeneratedQuizAttempts(attempt_id),
        CONSTRAINT FK_AttemptQuestions_Question FOREIGN KEY (question_id)
            REFERENCES dbo.GeneratedQuestions(question_id)
    );

    -- The lookup /quiz/answer makes on every graded question: "is this question part of
    -- this attempt?" The primary key already covers it, but attempts are also read
    -- whole when re-rendering, which this ordering supports.
    CREATE INDEX IX_AttemptQuestions_Attempt
        ON dbo.GeneratedQuizAttemptQuestions(attempt_id, sort_order);
END
GO
