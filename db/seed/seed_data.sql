-- seed_data.sql
-- Demo employees, courses, and history for Quadrant Technologies.
-- Run after org_seed.sql, which creates the Roles this references.
--
-- SAFE TO RE-RUN. Every insert is guarded and every foreign key is resolved by a natural
-- key — employees by email, roles by title, courses by title, questions by their text.
-- The previous version hardcoded surrogate ids (`employee_id 3`, `course_id 5`,
-- `question_id 7`), which held only on a database where these inserts had happened
-- exactly once, in exactly this order. That is not a property the seed can rely on now
-- that it runs from CI.
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
-- 1. EMPLOYEES
-- =========================================================
-- Inserted without manager_id, then linked in a second pass. Doing it in one pass would
-- reintroduce an ordering dependency — a manager has to exist before anyone points at
-- them — which is exactly what this rewrite removes.
INSERT INTO Employees (name, email, role_id, mastery_level, mastery_override, goal, company_id)
SELECT v.name, v.email, r.id, v.mastery_level, v.mastery_override, NULLIF(v.goal, ''), @company_id
FROM (VALUES
    ('Dana Whitfield',   'dana.whitfield@demo.com',  'Software Engineering Manager', 'Expert',   0, 'Grow team compliance rate to 100%'),
    ('Priya Nandakumar', 'priya.n@demo.com',         'VP of Sales',                  'Expert',   0, 'Improve team data handling compliance'),
    ('Ethan Brooks',     'ethan.brooks@demo.com',    'SDE 2',                        'Mediocre', 0, 'Become eligible for senior review'),
    ('Maya Osei',        'maya.osei@demo.com',       'SDE 1',                        'Beginner', 0, 'Finish onboarding requirements'),
    ('Liam Chen',        'liam.chen@demo.com',       'Security Analyst',             'Beginner', 0, ''),
    ('Sofia Delgado',    'sofia.delgado@demo.com',   'Account Executive',            'Mediocre', 0, 'Get certified on Customer Data Handling'),
    -- mastery_override = 1: set by a manager rather than earned
    ('Noah Whitaker',    'noah.whitaker@demo.com',   'Senior Account Executive',     'Expert',   1, ''),
    ('Ava Thompson',     'ava.thompson@demo.com',    'Account Executive',            'Beginner', 0, '')
) AS v(name, email, role_title, mastery_level, mastery_override, goal)
JOIN Roles r ON r.title = v.role_title
WHERE NOT EXISTS (SELECT 1 FROM Employees e WHERE e.email = v.email);

-- Reporting lines, by email on both sides.
UPDATE e
SET manager_id = m.id
FROM Employees e
JOIN (VALUES
    ('ethan.brooks@demo.com',   'dana.whitfield@demo.com'),
    ('maya.osei@demo.com',      'dana.whitfield@demo.com'),
    ('liam.chen@demo.com',      'dana.whitfield@demo.com'),
    ('sofia.delgado@demo.com',  'priya.n@demo.com'),
    ('noah.whitaker@demo.com',  'priya.n@demo.com'),
    ('ava.thompson@demo.com',   'priya.n@demo.com')
) AS v(email, manager_email) ON v.email = e.email
JOIN Employees m ON m.email = v.manager_email
WHERE e.manager_id IS NULL OR e.manager_id <> m.id;

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

-- =========================================================
-- 4. COMPLETIONS
-- =========================================================
-- Deliberately uneven, so the states the UI has to handle all exist in the data:
-- expired, expiring soon, failed-and-needs-retake, in progress, and untouched.
INSERT INTO Completions (employee_id, course_id, status, score_percent, completion_date,
                         expiry_date, certificate_url, reminder_sent_at)
SELECT e.id, c.id, v.status, v.score_percent,
       NULLIF(v.completion_date, ''), NULLIF(v.expiry_date, ''),
       NULLIF(v.certificate_url, ''), NULL
FROM (VALUES
    ('ethan.brooks@demo.com',   'Workplace Safety',           'completed',   100, '2025-08-10', '2026-08-10', 'https://example.com/certs/ethan-safety.pdf'),
    ('ethan.brooks@demo.com',   'Data Privacy Basics',        'completed',    90, '2025-08-12', '2026-08-12', 'https://example.com/certs/ethan-privacy.pdf'),
    ('ethan.brooks@demo.com',   'Cybersecurity Fundamentals', 'completed',    85, '2025-08-15', '2026-08-15', 'https://example.com/certs/ethan-cyber.pdf'),
    ('ethan.brooks@demo.com',   'Advanced Secure Coding',     'not_started', NULL, '',           '',           ''),
    ('maya.osei@demo.com',      'Workplace Safety',           'completed',    80, '2026-07-01', '2027-07-01', 'https://example.com/certs/maya-safety.pdf'),
    ('maya.osei@demo.com',      'Data Privacy Basics',        'in_progress', NULL, '',           '',           ''),
    ('maya.osei@demo.com',      'Anti-Harassment Training',   'not_started', NULL, '',           '',           ''),
    ('liam.chen@demo.com',      'Workplace Safety',           'failed',       60, '',           '',           ''),
    ('liam.chen@demo.com',      'Data Privacy Basics',        'not_started', NULL, '',           '',           ''),
    ('sofia.delgado@demo.com',  'Workplace Safety',           'completed',    95, '2025-08-20', '2026-08-20', 'https://example.com/certs/sofia-safety.pdf'),
    ('sofia.delgado@demo.com',  'Customer Data Handling',     'completed',    88, '2025-08-25', '2026-02-25', 'https://example.com/certs/sofia-data.pdf'),
    ('sofia.delgado@demo.com',  'Data Privacy Basics',        'in_progress', NULL, '',           '',           ''),
    ('noah.whitaker@demo.com',  'Workplace Safety',           'completed',   100, '2026-01-05', '2027-01-05', 'https://example.com/certs/noah-safety.pdf'),
    ('noah.whitaker@demo.com',  'Data Privacy Basics',        'completed',   100, '2026-01-06', '2027-01-06', 'https://example.com/certs/noah-privacy.pdf'),
    ('noah.whitaker@demo.com',  'Customer Data Handling',     'completed',    92, '2026-01-10', '2026-07-10', 'https://example.com/certs/noah-data.pdf'),
    ('ava.thompson@demo.com',   'Workplace Safety',           'not_started', NULL, '',           '',           ''),
    ('ava.thompson@demo.com',   'Customer Data Handling',     'not_started', NULL, '',           '',           '')
) AS v(email, course_title, status, score_percent, completion_date, expiry_date, certificate_url)
JOIN Employees e ON e.email = v.email
JOIN Courses   c ON c.title = v.course_title AND c.company_id = @company_id
WHERE NOT EXISTS (
    SELECT 1 FROM Completions x WHERE x.employee_id = e.id AND x.course_id = c.id
);

-- =========================================================
-- 5. QUIZ ATTEMPTS
-- =========================================================
INSERT INTO QuizAttempts (employee_id, question_id, attempt_number, submitted_answer, is_correct)
SELECT e.id, q.id, 1, v.submitted_answer, v.is_correct
FROM (VALUES
    ('ethan.brooks@demo.com', 'Who should you notify first if you discover a workplace hazard?', 'Your direct supervisor', 1),
    ('ethan.brooks@demo.com', 'What is the emergency assembly point used for?', 'Headcount after an evacuation', 1),
    ('ethan.brooks@demo.com', 'Fill in the blank: In case of fire, always use the stairs, never the ____.', 'elevator', 1),
    ('liam.chen@demo.com',    'Who should you notify first if you discover a workplace hazard?', 'A coworker in another department', 0),
    ('liam.chen@demo.com',    'What is the emergency assembly point used for?', 'Team meetings', 0),
    ('liam.chen@demo.com',    'Fill in the blank: In case of fire, always use the stairs, never the ____.', 'stairs', 0),
    ('sofia.delgado@demo.com', 'Customer payment data should be stored:', 'In an approved encrypted system only', 1),
    ('sofia.delgado@demo.com', 'Fill in the blank: Access to customer records should be logged for ____ purposes.', 'audit', 1)
) AS v(email, question_text, submitted_answer, is_correct)
JOIN Employees e ON e.email = v.email
JOIN QuizQuestions q ON q.question_text = v.question_text
WHERE NOT EXISTS (
    SELECT 1 FROM QuizAttempts a
    WHERE a.employee_id = e.id AND a.question_id = q.id AND a.attempt_number = 1
);

PRINT 'seed_data: '
    + CAST((SELECT COUNT(*) FROM Employees WHERE company_id = @company_id) AS VARCHAR) + ' employees, '
    + CAST((SELECT COUNT(*) FROM Courses   WHERE company_id = @company_id) AS VARCHAR) + ' courses.';
