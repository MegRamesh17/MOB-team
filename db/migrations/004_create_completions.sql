-- 004_create_completions.sql
-- One row per employee-course combo. Tracks status, score, expiry, and certificate.
-- expiry_date is computed by the app/Function when a completion is recorded
-- (completion_date + Courses.validity_months) — kept as a real column here so
-- the Timer-triggered expiry-check Function can query it directly with an index.

CREATE TABLE Completions (
    id                 INT IDENTITY(1,1) PRIMARY KEY,
    employee_id        INT           NOT NULL,
    course_id          INT           NOT NULL,
    status             NVARCHAR(20)  NOT NULL DEFAULT 'not_started'
                       CHECK (status IN ('not_started', 'in_progress', 'completed', 'failed')),
    score_percent      INT           NULL,               -- final quiz score, 0-100
    completion_date    DATETIME2     NULL,
    expiry_date        DATETIME2     NULL,
    certificate_url    NVARCHAR(500) NULL,                -- Blob Storage link, set on pass
    reminder_sent_at   DATETIME2     NULL,                -- prevents duplicate reminder emails

    CONSTRAINT FK_Completions_Employee FOREIGN KEY (employee_id)
        REFERENCES Employees(id) ON DELETE CASCADE,
    CONSTRAINT FK_Completions_Course FOREIGN KEY (course_id)
        REFERENCES Courses(id),
    CONSTRAINT UQ_Completions_Employee_Course UNIQUE (employee_id, course_id)
);

CREATE INDEX IX_Completions_Expiry ON Completions(expiry_date);
CREATE INDEX IX_Completions_Employee_Status ON Completions(employee_id, status);
