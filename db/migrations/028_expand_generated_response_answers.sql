-- 028_expand_generated_response_answers.sql
--
-- Pathway questions store free-text answers in GeneratedQuizAttemptQuestions as
-- NVARCHAR(MAX), then copy them into GeneratedQuizResponses when the assessment is
-- completed. The older response table allowed only 300 characters, so a valid short
-- answer could be accepted question-by-question and then fail at "See results".

IF OBJECT_ID('dbo.GeneratedQuizResponses', 'U') IS NOT NULL
   AND EXISTS
   (
       SELECT 1
         FROM sys.columns
        WHERE object_id = OBJECT_ID('dbo.GeneratedQuizResponses')
          AND name = 'text_answer'
          AND max_length <> -1
   )
BEGIN
    ALTER TABLE dbo.GeneratedQuizResponses
        ALTER COLUMN text_answer NVARCHAR(MAX) NULL;
END
GO
