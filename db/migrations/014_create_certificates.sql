CREATE TABLE Certificates (
    id              INT IDENTITY(1,1) PRIMARY KEY,
    employee_id     INT NOT NULL,
    course_id       INT NULL,
    attempt_id      NVARCHAR(50) NOT NULL,
    training_title  NVARCHAR(300) NULL,
    q_score         DECIMAL(5,2) NOT NULL,
    issued_at       DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    expires_at      DATETIME2 NOT NULL,
    certificate_url NVARCHAR(500) NULL,
    status          NVARCHAR(20) NOT NULL DEFAULT 'Active'
        CHECK (status IN ('Active','Expired','Revoked')),

    CONSTRAINT FK_Certificates_Employee FOREIGN KEY (employee_id) REFERENCES Employees(id)
);

CREATE INDEX IX_Certificates_Employee ON Certificates(employee_id);
