-- 010_create_readings_and_generated_quiz.sql
-- Matches content_agent.py's save_reading()/save_quiz() stubs exactly.
-- Supersedes the earlier CompanyDocuments draft, which assumed per-document
-- metadata tracking that the actual pdf_extractor.py/content_agent.py code
-- doesn't do -- it works at the container level, not individual PDFs.

CREATE TABLE Readings (
    id          INT IDENTITY(1,1) PRIMARY KEY,
    title       NVARCHAR(200) NOT NULL,
    content     NVARCHAR(MAX) NOT NULL,
    role_id     INT NULL,          -- looked up from Roles.title = the 'role' string
                                     -- content_agent.py passes in; NULL if no match found
    company_id  INT NOT NULL DEFAULT 1,   -- single-company for now, per content_agent.py's own note
    created_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT FK_Readings_Role    FOREIGN KEY (role_id)    REFERENCES Roles(id),
    CONSTRAINT FK_Readings_Company FOREIGN KEY (company_id) REFERENCES Companies(id)
);

CREATE TABLE GeneratedQuizQuestions (
    id              INT IDENTITY(1,1) PRIMARY KEY,
    reading_id      INT NOT NULL,
    question_text   NVARCHAR(1000) NOT NULL,
    choices         NVARCHAR(MAX) NOT NULL,   -- JSON array, matches the 'choices' list from content_agent.py
    correct_answer  NVARCHAR(500) NOT NULL,
    created_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT FK_GeneratedQuizQuestions_Reading FOREIGN KEY (reading_id) REFERENCES Readings(id) ON DELETE CASCADE,
    CONSTRAINT CK_GeneratedQuizQuestions_Choices_IsJson CHECK (ISJSON(choices) = 1)
);

CREATE INDEX IX_Readings_Role ON Readings(role_id);
CREATE INDEX IX_GeneratedQuizQuestions_Reading ON GeneratedQuizQuestions(reading_id);
