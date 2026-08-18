-- seed_data.sql
-- Courses and quiz questions for Quadrant Technologies.
-- Run after org_seed.sql, which creates the Roles these reference.
--
-- NO LONGER SEEDS PEOPLE. This used to invent eight @demo.com employees, their reporting
-- lines, their completion history and their quiz attempts, so there was something to look
-- at before real data existed. Real employees are loaded now, and the two sets shared the
-- same tables — so headcounts, Q Score rollups and org chart walks all mixed invented
-- people in with real ones, which is worse than no demo data because the numbers look
-- plausible. 024_remove_demo_employees.sql deletes the rows already in the database;
-- removing them here is what stops re-seeding putting them back.
--
-- Courses and questions stay. They are placeholder content rather than demo people, they
-- carry no @demo.com key, and dropping them would take real completion history with them.
--
-- SAFE TO RE-RUN. Every insert is guarded and every foreign key is resolved by a natural
-- key — roles by title, courses by title, questions by their text. The previous version
-- hardcoded surrogate ids (`course_id 5`, `question_id 7`), which held only on a database
-- where these inserts had happened exactly once, in exactly this order. That is not a
-- property the seed can rely on now that it runs from CI.
--
-- Placeholder content. Replace once there is real course material.

SET NOCOUNT ON;

DECLARE @company_id INT =
    (SELECT id FROM Companies WHERE name = 'Quadrant Technologies');

IF @company_id IS NULL
BEGIN
    RAISERROR('Companies is empty — run org_seed.sql before seed_data.sql.', 16, 1);
    RETURN;
END

-- =========================================================
-- 2. COURSES
-- =========================================================
INSERT INTO Courses (title, description, role_required_for, is_mandatory, validity_months,
                     passing_bar_percent, difficulty_level, estimated_minutes, resources, company_id)
SELECT v.title, v.description, v.role_required_for, v.is_mandatory, v.validity_months,
       v.passing_bar_percent, v.difficulty_level, v.estimated_minutes, v.resources, @company_id
FROM (VALUES
    ('Workplace Safety',
     'Covers emergency procedures, hazard reporting, and general workplace safety policy.',
     '["Software Engineer","QA Engineer","Sales Rep","Sales Manager","Engineering Manager"]',
     1, 12, 80, 'Beginner', 25,
     '[{"type":"video","title":"Workplace Safety Overview","url":"https://www.youtube.com/watch?v=example1"},{"type":"pdf","title":"Safety Handbook","url":"https://example.com/resources/safety-handbook.pdf"}]'),
    ('Data Privacy Basics',
     'Introduction to data protection principles, PII handling, and reporting a breach.',
     '["Software Engineer","QA Engineer","Sales Rep","Sales Manager","Engineering Manager"]',
     1, 12, 80, 'Beginner', 30,
     '[{"type":"video","title":"Data Privacy 101","url":"https://www.youtube.com/watch?v=example2"},{"type":"pdf","title":"Privacy Policy Summary","url":"https://example.com/resources/privacy-summary.pdf"}]'),
    ('Anti-Harassment Training',
     'Legal requirements and company policy on workplace conduct and reporting.',
     '["Software Engineer","QA Engineer","Sales Rep","Sales Manager","Engineering Manager"]',
     1, 12, 80, 'Beginner', 20,
     '[{"type":"video","title":"Respectful Workplace","url":"https://www.youtube.com/watch?v=example3"}]'),
    ('Customer Data Handling',
     'How to store, access, and dispose of customer data in compliance with policy.',
     '["Sales Rep","Sales Manager"]',
     1, 6, 80, 'Mediocre', 40,
     '[{"type":"pdf","title":"CRM Data Handling Guide","url":"https://example.com/resources/crm-data-guide.pdf"},{"type":"video","title":"Customer Data Best Practices","url":"https://www.youtube.com/watch?v=example4"}]'),
    ('Cybersecurity Fundamentals',
     'Phishing awareness, password hygiene, and secure coding basics.',
     '["Software Engineer","QA Engineer","Engineering Manager"]',
     1, 12, 80, 'Mediocre', 45,
     '[{"type":"video","title":"Phishing Awareness","url":"https://www.youtube.com/watch?v=example5"},{"type":"pdf","title":"Secure Coding Checklist","url":"https://example.com/resources/secure-coding.pdf"}]'),
    ('Advanced Secure Coding',
     'Deep dive into common vulnerabilities (SQL injection, XSS) and secure code review.',
     '["Software Engineer","QA Engineer"]',
     0, 12, 80, 'Expert', 60,
     '[{"type":"pdf","title":"OWASP Top 10 Summary","url":"https://example.com/resources/owasp-summary.pdf"}]'),
    ('Diversity & Inclusion',
     'Building an inclusive workplace culture and recognizing unconscious bias.',
     '["Software Engineer","QA Engineer","Sales Rep","Sales Manager","Engineering Manager"]',
     1, 24, 80, 'Beginner', 25,
     '[{"type":"video","title":"Inclusive Teams","url":"https://www.youtube.com/watch?v=example6"}]')
) AS v(title, description, role_required_for, is_mandatory, validity_months,
       passing_bar_percent, difficulty_level, estimated_minutes, resources)
WHERE NOT EXISTS (
    SELECT 1 FROM Courses c WHERE c.title = v.title AND c.company_id = @company_id
);

-- =========================================================
-- 3. QUIZ QUESTIONS
-- =========================================================
INSERT INTO QuizQuestions (course_id, question_text, question_type, options, correct_answer, points)
SELECT c.id, v.question_text, v.question_type, NULLIF(v.options, ''), v.correct_answer, 1
FROM (VALUES
    ('Workplace Safety', 'Who should you notify first if you discover a workplace hazard?', 'multiple_choice',
     '["Your direct supervisor","A coworker in another department","No one, fix it yourself","HR only"]', 'Your direct supervisor'),
    ('Workplace Safety', 'What is the emergency assembly point used for?', 'multiple_choice',
     '["Taking a break","Headcount after an evacuation","Team meetings","Fire drills only, not real emergencies"]', 'Headcount after an evacuation'),
    ('Workplace Safety', 'Fill in the blank: In case of fire, always use the stairs, never the ____.', 'fill_in_blank',
     '', 'elevator'),
    ('Data Privacy Basics', 'PII stands for:', 'multiple_choice',
     '["Personal Internet Information","Personally Identifiable Information","Private Internal Index","Protected Information Index"]', 'Personally Identifiable Information'),
    ('Data Privacy Basics', 'A data breach involving customer PII should be reported within:', 'multiple_choice',
     '["24 hours","1 week","30 days","No deadline"]', '24 hours'),
    ('Data Privacy Basics', 'Fill in the blank: Data should only be accessed on a ____-to-know basis.', 'fill_in_blank',
     '', 'need'),
    ('Anti-Harassment Training', 'Reports of harassment can be made to:', 'multiple_choice',
     '["HR only","Your manager or HR","No one until you have proof","External media"]', 'Your manager or HR'),
    ('Anti-Harassment Training', 'Retaliation against someone who reports harassment is:', 'multiple_choice',
     '["Allowed if the report was false","Prohibited by policy","Only prohibited for managers","Allowed after 90 days"]', 'Prohibited by policy'),
    ('Customer Data Handling', 'Customer payment data should be stored:', 'multiple_choice',
     '["In plaintext spreadsheets","In an approved encrypted system only","Emailed to the finance team","On a shared drive"]', 'In an approved encrypted system only'),
    ('Customer Data Handling', 'Fill in the blank: Access to customer records should be logged for ____ purposes.', 'fill_in_blank',
     '', 'audit'),
    ('Cybersecurity Fundamentals', 'A strong password should include:', 'multiple_choice',
     '["Only lowercase letters","Your name and birthdate","A mix of upper/lowercase, numbers, symbols","The word password123"]', 'A mix of upper/lowercase, numbers, symbols'),
    ('Cybersecurity Fundamentals', 'Fill in the blank: An email pretending to be from a trusted source to steal credentials is called ____.', 'fill_in_blank',
     '', 'phishing'),
    ('Advanced Secure Coding', 'Fill in the blank: Using parameterized queries helps prevent ____ injection attacks.', 'fill_in_blank',
     '', 'SQL'),
    ('Advanced Secure Coding', 'Which of the following best prevents XSS attacks?', 'multiple_choice',
     '["Storing passwords in plaintext","Escaping/sanitizing user input before rendering","Disabling HTTPS","Using longer session tokens"]', 'Escaping/sanitizing user input before rendering'),
    ('Diversity & Inclusion', 'Unconscious bias refers to:', 'multiple_choice',
     '["Intentional discrimination","Automatic judgments formed outside conscious awareness","A formal HR policy","A type of performance review"]', 'Automatic judgments formed outside conscious awareness')
) AS v(course_title, question_text, question_type, options, correct_answer)
JOIN Courses c ON c.title = v.course_title AND c.company_id = @company_id
WHERE NOT EXISTS (
    SELECT 1 FROM QuizQuestions q WHERE q.course_id = c.id AND q.question_text = v.question_text
);

DECLARE @employee_count INT, @course_count INT, @can_sign_in INT;

SELECT @employee_count = COUNT(*) FROM Employees WHERE company_id = @company_id;
SELECT @course_count = COUNT(*) FROM Courses WHERE company_id = @company_id;
-- Reported because a seed that loads people who cannot sign in looks identical to a
-- working one until someone tries the login screen.
SELECT @can_sign_in = COUNT(*) FROM Employees
 WHERE company_id = @company_id AND password_hash IS NOT NULL;

PRINT 'seed_data: '
    + CAST(@employee_count AS VARCHAR) + ' employees ('
    + CAST(@can_sign_in AS VARCHAR) + ' can sign in), '
    + CAST(@course_count AS VARCHAR) + ' courses.';
