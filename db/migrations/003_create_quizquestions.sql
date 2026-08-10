-- 003_create_quizquestions.sql
-- Quiz bank per course. Supports multiple-choice and fill-in-the-blank.
-- For multiple_choice: options is a JSON array of strings, correct_answer is one of them.
-- For fill_in_blank: options is NULL, correct_answer is the exact expected text/code.

CREATE TABLE QuizQuestions (
    id              INT IDENTITY(1,1) PRIMARY KEY,
    course_id       INT            NOT NULL,
    question_text   NVARCHAR(1000) NOT NULL,
    question_type   NVARCHAR(20)   NOT NULL
                    CHECK (question_type IN ('multiple_choice', 'fill_in_blank')),
    options         NVARCHAR(MAX)  NULL,   -- JSON array, only for multiple_choice
    correct_answer  NVARCHAR(500)  NOT NULL,
    points          INT            NOT NULL DEFAULT 1,

    CONSTRAINT FK_QuizQuestions_Course FOREIGN KEY (course_id)
        REFERENCES Courses(id) ON DELETE CASCADE,
    CONSTRAINT CK_QuizQuestions_Options_IsJson CHECK (ISJSON(options) = 1 OR options IS NULL)
);

CREATE INDEX IX_QuizQuestions_Course ON QuizQuestions(course_id);
