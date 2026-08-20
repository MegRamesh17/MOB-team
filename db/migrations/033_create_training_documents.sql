-- 033_create_training_documents.sql
--
-- One row per uploaded/crawled source document. SourceChunks is intentionally at a
-- different grain, so it cannot answer who added a course without repeating ownership
-- on every chunk. This registry supplies that missing ownership boundary and gives the
-- delete endpoint a stable document id that is not a mutable AI-generated title.
--
-- pending_analysis_json also lives here rather than in a separate table: the AI's
-- proposed section->role mapping is exactly document-grain state, and a document has
-- at most one open proposal at a time (a second upload of the same title collides on
-- doc_title before it ever gets here). Written when analyze_document() succeeds,
-- cleared the moment confirm_document() tags the chunks -- so a manager who navigates
-- away from the mapping-review screen before confirming can come back and see the
-- same proposal again instead of it vanishing with the unmounted component.

IF OBJECT_ID('dbo.TrainingDocuments', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.TrainingDocuments
    (
        company_id           INT            NOT NULL,
        document_id          NVARCHAR(64)   NOT NULL,
        doc_title            NVARCHAR(300)  NOT NULL,
        uploaded_by          INT            NULL,
        source_kind          NVARCHAR(20)   NOT NULL DEFAULT 'upload',
        source_label         NVARCHAR(1000) NULL,
        trusted_link_id      INT            NULL,
        pending_analysis_json NVARCHAR(MAX) NULL,
        created_at           DATETIME2(3)   NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_TrainingDocuments PRIMARY KEY (company_id, document_id),
        CONSTRAINT FK_TrainingDocuments_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id),
        CONSTRAINT FK_TrainingDocuments_Uploader FOREIGN KEY (uploaded_by)
            REFERENCES dbo.Employees(id),
        CONSTRAINT FK_TrainingDocuments_TrustedLink FOREIGN KEY (trusted_link_id)
            REFERENCES dbo.TrustedLinks(id),
        CONSTRAINT CK_TrainingDocuments_SourceKind CHECK
            (source_kind IN ('upload', 'trusted_link', 'legacy'))
    );

    CREATE INDEX IX_TrainingDocuments_Title
        ON dbo.TrainingDocuments(company_id, doc_title);
END
GO

-- Existing documents predate uploader tracking. They remain deletable by an admin or
-- executive, while ordinary managers may only delete new rows that name them as owner.
-- generated-lessons is assessment material derived from a source and must not become a
-- second document row.
INSERT INTO dbo.TrainingDocuments
    (company_id, document_id, doc_title, uploaded_by, source_kind, source_label)
SELECT source.company_id, source.doc_id, MAX(source.doc_title), NULL, 'legacy',
       MAX(COALESCE(source.source_url, source.doc_title))
  FROM dbo.SourceChunks AS source
 WHERE COALESCE(source.container, '') <> 'generated-lessons'
   AND NOT EXISTS
       (
           SELECT 1
             FROM dbo.TrainingDocuments AS existing
            WHERE existing.company_id = source.company_id
              AND existing.document_id = source.doc_id
       )
 GROUP BY source.company_id, source.doc_id;
GO

-- A running job is the one other in-progress state a document can be in besides "has a
-- pending mapping proposal" -- and it isn't tracked in TrainingDocuments at all, it's
-- read straight off GenerationJobs by list_documents already. Nothing to add here for
-- that case.
