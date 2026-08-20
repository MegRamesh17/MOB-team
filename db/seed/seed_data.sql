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
    ('Dana Whitfield',   'dana.whitfield@quizrant.com',  'Software Engineering Manager', 'Expert',   0, 'Grow team compliance rate to 100%'),
    ('Priya Nandakumar', 'priya.n@quizrant.com',         'VP of Sales',                  'Expert',   0, 'Improve team data handling compliance'),
    ('Ethan Brooks',     'ethan.brooks@quizrant.com',    'SDE 2',                        'Mediocre', 0, 'Become eligible for senior review'),
    ('Maya Osei',        'maya.osei@quizrant.com',       'SDE 1',                        'Beginner', 0, 'Finish onboarding requirements'),
    ('Liam Chen',        'liam.chen@quizrant.com',       'Security Analyst',             'Beginner', 0, ''),
    ('Sofia Delgado',    'sofia.delgado@quizrant.com',   'Account Executive',            'Mediocre', 0, 'Get certified on Customer Data Handling'),
    -- mastery_override = 1: set by a manager rather than earned
    ('Noah Whitaker',    'noah.whitaker@quizrant.com',   'Senior Account Executive',     'Expert',   1, ''),
    ('Ava Thompson',     'ava.thompson@quizrant.com',    'Account Executive',            'Beginner', 0, ''),

    -- Fills out the rest of the Software Development department (Cybersecurity,
    -- Software Engineering, DevOps) so every role in the org chart has a real person
    -- in it, not just the four that happened to be here already. Reporting lines are
    -- set below, in the manager pass.
    ('Marcus Chen',      'marcus.chen@quizrant.com',     'Chief Technology Officer',        'Expert',   0, ''),
    ('Renu Kapoor',      'renu.kapoor@quizrant.com',     'Director of Cybersecurity',       'Expert',   0, 'Close the SOC 2 gap analysis by Q3'),
    ('Owen Bennett',     'owen.bennett@quizrant.com',    'Senior Security Architect',       'Expert',   0, ''),
    ('Farah Haddad',     'farah.haddad@quizrant.com',    'Information Security Engineer',   'Mediocre', 0, 'Get certified on Incident Response'),
    ('Wei Zhang',        'wei.zhang@quizrant.com',       'Director of Software Engineering','Expert',   0, ''),
    ('Jordan Ellis',     'jordan.ellis@quizrant.com',    'SDE 3',                           'Expert',   0, 'Mentor two SDE 1s this quarter'),
    ('Naomi Reyes',      'naomi.reyes@quizrant.com',     'Director of DevOps',              'Expert',   0, ''),
    ('Ben Novak',        'ben.novak@quizrant.com',       'Senior DevOps',                   'Expert',   0, ''),
    ('Grace Kim',        'grace.kim@quizrant.com',       'DevOps Engineer',                 'Mediocre', 0, 'Finish on-call onboarding'),
    ('Diego Martins',    'diego.martins@quizrant.com',   'Junior DevOps',                   'Beginner', 0, 'Finish onboarding requirements'),
    ('Audience Intern 01', 'intern01@quizrant.com',      'Engineering Intern',              'Beginner', 0, 'Complete the audience training'),
    ('Audience Intern 02', 'intern02@quizrant.com',      'Engineering Intern',              'Beginner', 0, 'Complete the audience training'),
    ('Audience Intern 03', 'intern03@quizrant.com',      'Engineering Intern',              'Beginner', 0, 'Complete the audience training'),
    ('Audience Intern 04', 'intern04@quizrant.com',      'Engineering Intern',              'Beginner', 0, 'Complete the audience training'),
    ('Audience Intern 05', 'intern05@quizrant.com',      'Engineering Intern',              'Beginner', 0, 'Complete the audience training'),
    ('Audience Intern 06', 'intern06@quizrant.com',      'Engineering Intern',              'Beginner', 0, 'Complete the audience training'),
    ('Audience Intern 07', 'intern07@quizrant.com',      'Engineering Intern',              'Beginner', 0, 'Complete the audience training'),
    ('Audience Intern 08', 'intern08@quizrant.com',      'Engineering Intern',              'Beginner', 0, 'Complete the audience training'),
    ('Audience Intern 09', 'intern09@quizrant.com',      'Engineering Intern',              'Beginner', 0, 'Complete the audience training'),
    ('Audience Intern 10', 'intern10@quizrant.com',      'Engineering Intern',              'Beginner', 0, 'Complete the audience training')
) AS v(name, email, role_title, mastery_level, mastery_override, goal)
JOIN Roles r ON r.title = v.role_title
WHERE NOT EXISTS (SELECT 1 FROM Employees e WHERE e.email = v.email);

-- Reporting lines, by email on both sides.
UPDATE e
SET manager_id = m.id
FROM Employees e
JOIN (VALUES
    -- Software Engineering: SDE 1/2/3 -> Dana (SWE Manager) -> Wei (Director) -> Marcus (CTO)
    ('ethan.brooks@quizrant.com',   'dana.whitfield@quizrant.com'),
    ('maya.osei@quizrant.com',      'dana.whitfield@quizrant.com'),
    ('jordan.ellis@quizrant.com',   'dana.whitfield@quizrant.com'),
    ('dana.whitfield@quizrant.com', 'wei.zhang@quizrant.com'),
    ('wei.zhang@quizrant.com',      'marcus.chen@quizrant.com'),

    -- Cybersecurity: Security Analyst / Senior Security Architect / Information
    -- Security Engineer all report directly to the Director (no manager layer in
    -- between, unlike Software Engineering) -> Marcus (CTO).
    -- liam.chen previously reported to dana.whitfield (Software Engineering's
    -- manager), which was never actually correct for a Security Analyst -- this
    -- corrects it to the real Cybersecurity chain.
    ('liam.chen@quizrant.com',      'renu.kapoor@quizrant.com'),
    ('owen.bennett@quizrant.com',   'renu.kapoor@quizrant.com'),
    ('farah.haddad@quizrant.com',   'renu.kapoor@quizrant.com'),
    ('renu.kapoor@quizrant.com',    'marcus.chen@quizrant.com'),

    -- DevOps: Senior/regular/Junior DevOps all report directly to the Director -> Marcus (CTO)
    ('ben.novak@quizrant.com',      'naomi.reyes@quizrant.com'),
    ('grace.kim@quizrant.com',      'naomi.reyes@quizrant.com'),
    ('diego.martins@quizrant.com',  'naomi.reyes@quizrant.com'),
    ('naomi.reyes@quizrant.com',    'marcus.chen@quizrant.com'),

    -- Audience demo accounts use one low-privilege training role and report to Dana,
    -- so Dana can assign one INTERN course to all ten at once.
    ('intern01@quizrant.com',       'dana.whitfield@quizrant.com'),
    ('intern02@quizrant.com',       'dana.whitfield@quizrant.com'),
    ('intern03@quizrant.com',       'dana.whitfield@quizrant.com'),
    ('intern04@quizrant.com',       'dana.whitfield@quizrant.com'),
    ('intern05@quizrant.com',       'dana.whitfield@quizrant.com'),
    ('intern06@quizrant.com',       'dana.whitfield@quizrant.com'),
    ('intern07@quizrant.com',       'dana.whitfield@quizrant.com'),
    ('intern08@quizrant.com',       'dana.whitfield@quizrant.com'),
    ('intern09@quizrant.com',       'dana.whitfield@quizrant.com'),
    ('intern10@quizrant.com',       'dana.whitfield@quizrant.com'),

    -- Sales (unchanged)
    ('sofia.delgado@quizrant.com',  'priya.n@quizrant.com'),
    ('noah.whitaker@quizrant.com',  'priya.n@quizrant.com'),
    ('ava.thompson@quizrant.com',   'priya.n@quizrant.com')
) AS v(email, manager_email) ON v.email = e.email
JOIN Employees m ON m.email = v.manager_email
WHERE e.manager_id IS NULL OR e.manager_id <> m.id;

-- These are presentation logins, not real inboxes. Do not spend time or provider quota
-- attempting assignment and expiry emails for them.
UPDATE Employees
   SET notifications_enabled = 0
 WHERE email IN
       ('intern01@quizrant.com', 'intern02@quizrant.com', 'intern03@quizrant.com',
        'intern04@quizrant.com', 'intern05@quizrant.com', 'intern06@quizrant.com',
        'intern07@quizrant.com', 'intern08@quizrant.com', 'intern09@quizrant.com',
        'intern10@quizrant.com');

-- Intentionally public presentation credentials: intern01 uses password1 through
-- intern10 using password10. Store only bcrypt hashes and scope the update to both the
-- ten explicit emails and the low-privilege INTERN role so no real account can be reset.
UPDATE employee
   SET password_hash = credential.password_hash
  FROM dbo.Employees AS employee
  JOIN dbo.Roles AS role ON role.id = employee.role_id
  JOIN (VALUES
    ('intern01@quizrant.com', '$2b$12$TXbSHNi.JK3hYUtMMMuS0uxh4FbJWXlgaFb9tR7CjjLKA79JKV4xy'),
    ('intern02@quizrant.com', '$2b$12$I32leJi/xUDP1v2SiO6b/e5fTVOnznEvWIAHJckBULWPmza6l1v8O'),
    ('intern03@quizrant.com', '$2b$12$C0VolDjo68j/ed9EifFEv.Ie8NzwwJ.U7bv5s6hyVuEO/.7J7/hTK'),
    ('intern04@quizrant.com', '$2b$12$bmaeWZ9j6LY8MLXIwfyuSeH4yUDbvgU7msOr0sm2qvBUCnppwMhwS'),
    ('intern05@quizrant.com', '$2b$12$NlqMUi82bMGmJ1/dCDYl7OiC2aMy7bsxw69mObMuWYqSaNBeMA78G'),
    ('intern06@quizrant.com', '$2b$12$sB5uw4sCHqzbQTF5qIT/n.ERGwZ.WOm6mrYUXrfrJB1Xy.Mg/pkk2'),
    ('intern07@quizrant.com', '$2b$12$UlRoUoljfi.C4bf52A9x8OHQuYm.qTtmfSQ8bnEvBCxMqSoNYuFsK'),
    ('intern08@quizrant.com', '$2b$12$vXjaYGEvIoZLwqlqrOcwK.56oPCyZsWJtQbNd8IBW4szMaUU0nFcq'),
    ('intern09@quizrant.com', '$2b$12$vTjqw4kbVEd46iO1CHJ36e3JfI7IYqM3O8ObrW.wyyfO38bjiAchS'),
    ('intern10@quizrant.com', '$2b$12$SvzVZSQH4K5LbrOIMtWRUuNU3YXmDraBQclNUZ5n872qK3UGJnfIq')
  ) AS credential(email, password_hash) ON credential.email = employee.email
 WHERE role.role_code = 'INTERN';

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
    ('ethan.brooks@quizrant.com',   'Workplace Safety',           'completed',   100, '2025-08-10', '2026-08-10', 'https://example.com/certs/ethan-safety.pdf'),
    ('ethan.brooks@quizrant.com',   'Data Privacy Basics',        'completed',    90, '2025-08-12', '2026-08-12', 'https://example.com/certs/ethan-privacy.pdf'),
    ('ethan.brooks@quizrant.com',   'Cybersecurity Fundamentals', 'completed',    85, '2025-08-15', '2026-08-15', 'https://example.com/certs/ethan-cyber.pdf'),
    ('ethan.brooks@quizrant.com',   'Advanced Secure Coding',     'not_started', NULL, '',           '',           ''),
    ('maya.osei@quizrant.com',      'Workplace Safety',           'completed',    80, '2026-07-01', '2027-07-01', 'https://example.com/certs/maya-safety.pdf'),
    ('maya.osei@quizrant.com',      'Data Privacy Basics',        'in_progress', NULL, '',           '',           ''),
    ('maya.osei@quizrant.com',      'Anti-Harassment Training',   'not_started', NULL, '',           '',           ''),
    ('liam.chen@quizrant.com',      'Workplace Safety',           'failed',       60, '',           '',           ''),
    ('liam.chen@quizrant.com',      'Data Privacy Basics',        'not_started', NULL, '',           '',           ''),
    ('sofia.delgado@quizrant.com',  'Workplace Safety',           'completed',    95, '2025-08-20', '2026-08-20', 'https://example.com/certs/sofia-safety.pdf'),
    ('sofia.delgado@quizrant.com',  'Customer Data Handling',     'completed',    88, '2025-08-25', '2026-02-25', 'https://example.com/certs/sofia-data.pdf'),
    ('sofia.delgado@quizrant.com',  'Data Privacy Basics',        'in_progress', NULL, '',           '',           ''),
    ('noah.whitaker@quizrant.com',  'Workplace Safety',           'completed',   100, '2026-01-05', '2027-01-05', 'https://example.com/certs/noah-safety.pdf'),
    ('noah.whitaker@quizrant.com',  'Data Privacy Basics',        'completed',   100, '2026-01-06', '2027-01-06', 'https://example.com/certs/noah-privacy.pdf'),
    ('noah.whitaker@quizrant.com',  'Customer Data Handling',     'completed',    92, '2026-01-10', '2026-07-10', 'https://example.com/certs/noah-data.pdf'),
    ('ava.thompson@quizrant.com',   'Workplace Safety',           'not_started', NULL, '',           '',           ''),
    ('ava.thompson@quizrant.com',   'Customer Data Handling',     'not_started', NULL, '',           '',           '')
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
    ('ethan.brooks@quizrant.com', 'Who should you notify first if you discover a workplace hazard?', 'Your direct supervisor', 1),
    ('ethan.brooks@quizrant.com', 'What is the emergency assembly point used for?', 'Headcount after an evacuation', 1),
    ('ethan.brooks@quizrant.com', 'Fill in the blank: In case of fire, always use the stairs, never the ____.', 'elevator', 1),
    ('liam.chen@quizrant.com',    'Who should you notify first if you discover a workplace hazard?', 'A coworker in another department', 0),
    ('liam.chen@quizrant.com',    'What is the emergency assembly point used for?', 'Team meetings', 0),
    ('liam.chen@quizrant.com',    'Fill in the blank: In case of fire, always use the stairs, never the ____.', 'stairs', 0),
    ('sofia.delgado@quizrant.com', 'Customer payment data should be stored:', 'In an approved encrypted system only', 1),
    ('sofia.delgado@quizrant.com', 'Fill in the blank: Access to customer records should be logged for ____ purposes.', 'audit', 1)
) AS v(email, question_text, submitted_answer, is_correct)
JOIN Employees e ON e.email = v.email
JOIN QuizQuestions q ON q.question_text = v.question_text
WHERE NOT EXISTS (
    SELECT 1 FROM QuizAttempts a
    WHERE a.employee_id = e.id AND a.question_id = q.id AND a.attempt_number = 1
);

DECLARE @employee_count INT, @course_count INT;

SELECT @employee_count = COUNT(*) FROM Employees WHERE company_id = @company_id;
SELECT @course_count = COUNT(*) FROM Courses WHERE company_id = @company_id;

PRINT 'seed_data: '
    + CAST(@employee_count AS VARCHAR) + ' employees, '
    + CAST(@course_count AS VARCHAR) + ' courses.';
