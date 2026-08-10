-- 005_create_quizattempts.sql
-- Individual answer log, one row per question per attempt. Lets you compute
-- per-question stats (e.g. "80% of employees miss Q3") and supports retakes,
-- since attempt_number distinguishes multiple tries at the same course.

CREATE TABLE QuizAttempts (
    id              INT           IDENTITY(1,1) PRIMARY KEY,
    employee_id     INT           NOT NULL,
    question_id     INT           NOT NULL,
    attempt_number  INT           NOT NULL DEFAULT 1,
    submitted_answer NVARCHAR(500) NOT NULL,
    is_correct      BIT           NOT NULL,
    submitted_at    DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT FK_QuizAttempts_Employee FOREIGN KEY (employee_id)
        REFERENCES Employees(id) ON DELETE CASCADE,
    CONSTRAINT FK_QuizAttempts_Question FOREIGN KEY (question_id)
        REFERENCES QuizQuestions(id)
);

CREATE INDEX IX_QuizAttempts_Employee ON QuizAttempts(employee_id);
CREATE INDEX IX_QuizAttempts_Question ON QuizAttempts(question_id);
