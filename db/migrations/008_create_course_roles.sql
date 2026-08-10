-- 008_create_course_roles.sql
-- Replaces Courses.role_required_for (JSON strings) with a real many-to-many
-- join against Roles.id -- now that Roles exists, this is safer than text matching.

CREATE TABLE CourseRoles (
    course_id  INT NOT NULL,
    role_id    INT NOT NULL,

    PRIMARY KEY (course_id, role_id),
    CONSTRAINT FK_CourseRoles_Course FOREIGN KEY (course_id) REFERENCES Courses(id),
    CONSTRAINT FK_CourseRoles_Role   FOREIGN KEY (role_id)   REFERENCES Roles(id)
);

-- Once CourseRoles is populated for every course, the old JSON column can go:
-- ALTER TABLE Courses DROP COLUMN role_required_for;
