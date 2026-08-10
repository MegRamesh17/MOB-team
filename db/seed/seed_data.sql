-- seed_data.sql
-- Placeholder demo data. Safe to re-run on a fresh DB (run migrations first).
-- Replace with real content once your team decides on the content source.

-- =========================================================
-- 1. EMPLOYEES (managers first, so manager_id can reference them)
-- =========================================================
-- Uses role_id (looked up by title from the real org_seed.sql roles) instead
-- of free-text role/department, matching the schema from migrations 006-009.
-- Run org_seed.sql BEFORE this file, or these subqueries will return NULL.

INSERT INTO Employees (name, email, role_id, manager_id, mastery_level, mastery_override, goal, company_id) VALUES
('Dana Whitfield', 'dana.whitfield@demo.com', (SELECT id FROM Roles WHERE title = 'Software Engineering Manager'), NULL, 'Expert', 0, 'Grow team compliance rate to 100%', 1),
('Priya Nandakumar', 'priya.n@demo.com', (SELECT id FROM Roles WHERE title = 'VP of Sales'), NULL, 'Expert', 0, 'Improve team data handling compliance', 1),

('Ethan Brooks', 'ethan.brooks@demo.com', (SELECT id FROM Roles WHERE title = 'SDE 2'), 1, 'Mediocre', 0, 'Become eligible for senior review', 1),
('Maya Osei', 'maya.osei@demo.com', (SELECT id FROM Roles WHERE title = 'SDE 1'), 1, 'Beginner', 0, 'Finish onboarding requirements', 1),
('Liam Chen', 'liam.chen@demo.com', (SELECT id FROM Roles WHERE title = 'Security Analyst'), 1, 'Beginner', 0, NULL, 1),

('Sofia Delgado', 'sofia.delgado@demo.com', (SELECT id FROM Roles WHERE title = 'Account Executive'), 2, 'Mediocre', 0, 'Get certified on Customer Data Handling', 1),
('Noah Whitaker', 'noah.whitaker@demo.com', (SELECT id FROM Roles WHERE title = 'Senior Account Executive'), 2, 'Expert', 1, NULL, 1),  -- manually overridden by manager
('Ava Thompson', 'ava.thompson@demo.com', (SELECT id FROM Roles WHERE title = 'Account Executive'), 2, 'Beginner', 0, NULL, 1);

-- =========================================================
-- 2. COURSES
-- =========================================================
-- company_id is set via the UPDATE right after this insert, to avoid
-- editing every multi-line VALUES tuple below (several contain JSON strings).
INSERT INTO Courses (title, description, role_required_for, is_mandatory, validity_months, passing_bar_percent, difficulty_level, estimated_minutes, resources) VALUES
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
 '[{"type":"video","title":"Inclusive Teams","url":"https://www.youtube.com/watch?v=example6"}]');

-- Tag all 7 courses just inserted as belonging to Quadrant Technologies (company_id 1)
UPDATE Courses SET company_id = 1 WHERE company_id IS NULL;

-- =========================================================
-- 3. QUIZ QUESTIONS (mix of multiple_choice and fill_in_blank)
-- =========================================================

-- Workplace Safety (course_id = 1)
INSERT INTO QuizQuestions (course_id, question_text, question_type, options, correct_answer, points) VALUES
(1, 'Who should you notify first if you discover a workplace hazard?', 'multiple_choice',
 '["Your direct supervisor","A coworker in another department","No one, fix it yourself","HR only"]', 'Your direct supervisor', 1),
(1, 'What is the emergency assembly point used for?', 'multiple_choice',
 '["Taking a break","Headcount after an evacuation","Team meetings","Fire drills only, not real emergencies"]', 'Headcount after an evacuation', 1),
(1, 'Fill in the blank: In case of fire, always use the stairs, never the ____.', 'fill_in_blank',
 NULL, 'elevator', 1);

-- Data Privacy Basics (course_id = 2)
INSERT INTO QuizQuestions (course_id, question_text, question_type, options, correct_answer, points) VALUES
(2, 'PII stands for:', 'multiple_choice',
 '["Personal Internet Information","Personally Identifiable Information","Private Internal Index","Protected Information Index"]', 'Personally Identifiable Information', 1),
(2, 'A data breach involving customer PII should be reported within:', 'multiple_choice',
 '["24 hours","1 week","30 days","No deadline"]', '24 hours', 1),
(2, 'Fill in the blank: Data should only be accessed on a ____-to-know basis.', 'fill_in_blank',
 NULL, 'need', 1);

-- Anti-Harassment Training (course_id = 3)
INSERT INTO QuizQuestions (course_id, question_text, question_type, options, correct_answer, points) VALUES
(3, 'Reports of harassment can be made to:', 'multiple_choice',
 '["HR only","Your manager or HR","No one until you have proof","External media"]', 'Your manager or HR', 1),
(3, 'Retaliation against someone who reports harassment is:', 'multiple_choice',
 '["Allowed if the report was false","Prohibited by policy","Only prohibited for managers","Allowed after 90 days"]', 'Prohibited by policy', 1);

-- Customer Data Handling (course_id = 4)
INSERT INTO QuizQuestions (course_id, question_text, question_type, options, correct_answer, points) VALUES
(4, 'Customer payment data should be stored:', 'multiple_choice',
 '["In plaintext spreadsheets","In an approved encrypted system only","Emailed to the finance team","On a shared drive"]', 'In an approved encrypted system only', 1),
(4, 'Fill in the blank: Access to customer records should be logged for ____ purposes.', 'fill_in_blank',
 NULL, 'audit', 1);

-- Cybersecurity Fundamentals (course_id = 5)
INSERT INTO QuizQuestions (course_id, question_text, question_type, options, correct_answer, points) VALUES
(5, 'A strong password should include:', 'multiple_choice',
 '["Only lowercase letters","Your name and birthdate","A mix of upper/lowercase, numbers, symbols","The word password123"]', 'A mix of upper/lowercase, numbers, symbols', 1),
(5, 'Fill in the blank: An email pretending to be from a trusted source to steal credentials is called ____.', 'fill_in_blank',
 NULL, 'phishing', 1);

-- Advanced Secure Coding (course_id = 6)
INSERT INTO QuizQuestions (course_id, question_text, question_type, options, correct_answer, points) VALUES
(6, 'Fill in the blank: Using parameterized queries helps prevent ____ injection attacks.', 'fill_in_blank',
 NULL, 'SQL', 1),
(6, 'Which of the following best prevents XSS attacks?', 'multiple_choice',
 '["Storing passwords in plaintext","Escaping/sanitizing user input before rendering","Disabling HTTPS","Using longer session tokens"]', 'Escaping/sanitizing user input before rendering', 1);

-- Diversity & Inclusion (course_id = 7)
INSERT INTO QuizQuestions (course_id, question_text, question_type, options, correct_answer, points) VALUES
(7, 'Unconscious bias refers to:', 'multiple_choice',
 '["Intentional discrimination","Automatic judgments formed outside conscious awareness","A formal HR policy","A type of performance review"]', 'Automatic judgments formed outside conscious awareness', 1);

-- =========================================================
-- 4. COMPLETIONS (mix of statuses, some expiring soon, some overdue)
-- =========================================================
-- Ethan Brooks (id 3) — mostly complete, one nearing expiry
INSERT INTO Completions (employee_id, course_id, status, score_percent, completion_date, expiry_date, certificate_url, reminder_sent_at) VALUES
(3, 1, 'completed', 100, '2025-08-10', '2026-08-10', 'https://example.com/certs/emp3-course1.pdf', NULL),
(3, 2, 'completed', 90,  '2025-08-12', '2026-08-12', 'https://example.com/certs/emp3-course2.pdf', NULL),
(3, 5, 'completed', 85,  '2025-08-15', '2026-08-15', 'https://example.com/certs/emp3-course5.pdf', NULL),
(3, 6, 'not_started', NULL, NULL, NULL, NULL, NULL);

-- Maya Osei (id 4) — new hire, still working through mandatory basics
INSERT INTO Completions (employee_id, course_id, status, score_percent, completion_date, expiry_date, certificate_url, reminder_sent_at) VALUES
(4, 1, 'completed', 80, '2026-07-01', '2027-07-01', 'https://example.com/certs/emp4-course1.pdf', NULL),
(4, 2, 'in_progress', NULL, NULL, NULL, NULL, NULL),
(4, 3, 'not_started', NULL, NULL, NULL, NULL, NULL);

-- Liam Chen (id 5) — failed a quiz once, needs retake
INSERT INTO Completions (employee_id, course_id, status, score_percent, completion_date, expiry_date, certificate_url, reminder_sent_at) VALUES
(5, 1, 'failed', 60, NULL, NULL, NULL, NULL),
(5, 2, 'not_started', NULL, NULL, NULL, NULL, NULL);

-- Sofia Delgado (id 6) — cert expiring within 30 days (for testing the reminder Function)
INSERT INTO Completions (employee_id, course_id, status, score_percent, completion_date, expiry_date, certificate_url, reminder_sent_at) VALUES
(6, 1, 'completed', 95, '2025-08-20', '2026-08-20', 'https://example.com/certs/emp6-course1.pdf', NULL),
(6, 4, 'completed', 88, '2025-08-25', '2026-02-25', 'https://example.com/certs/emp6-course4.pdf', NULL),
(6, 2, 'in_progress', NULL, NULL, NULL, NULL, NULL);

-- Noah Whitaker (id 7) — fully compliant, expert override
INSERT INTO Completions (employee_id, course_id, status, score_percent, completion_date, expiry_date, certificate_url, reminder_sent_at) VALUES
(7, 1, 'completed', 100, '2026-01-05', '2027-01-05', 'https://example.com/certs/emp7-course1.pdf', NULL),
(7, 2, 'completed', 100, '2026-01-06', '2027-01-06', 'https://example.com/certs/emp7-course2.pdf', NULL),
(7, 4, 'completed', 92,  '2026-01-10', '2026-07-10', 'https://example.com/certs/emp7-course4.pdf', NULL);

-- Ava Thompson (id 8) — brand new, nothing started
INSERT INTO Completions (employee_id, course_id, status, score_percent, completion_date, expiry_date, certificate_url, reminder_sent_at) VALUES
(8, 1, 'not_started', NULL, NULL, NULL, NULL, NULL),
(8, 4, 'not_started', NULL, NULL, NULL, NULL, NULL);

-- =========================================================
-- 5. QUIZ ATTEMPTS (individual answer log, ties back to QuizQuestions)
-- =========================================================
-- Ethan Brooks (employee_id 3) on Workplace Safety questions (1,2,3)
INSERT INTO QuizAttempts (employee_id, question_id, attempt_number, submitted_answer, is_correct) VALUES
(3, 1, 1, 'Your direct supervisor', 1),
(3, 2, 1, 'Headcount after an evacuation', 1),
(3, 3, 1, 'elevator', 1);

-- Liam Chen (employee_id 5) failed attempt on Workplace Safety
INSERT INTO QuizAttempts (employee_id, question_id, attempt_number, submitted_answer, is_correct) VALUES
(5, 1, 1, 'A coworker in another department', 0),
(5, 2, 1, 'Team meetings', 0),
(5, 3, 1, 'stairs', 0);

-- Sofia Delgado (employee_id 6) on Customer Data Handling questions (7,8)
INSERT INTO QuizAttempts (employee_id, question_id, attempt_number, submitted_answer, is_correct) VALUES
(6, 7, 1, 'In an approved encrypted system only', 1),
(6, 8, 1, 'audit', 1);
