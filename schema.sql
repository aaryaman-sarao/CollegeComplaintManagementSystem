-- =============================================================
-- Smart College Complaint Management System
-- Phase 1: MySQL Database Schema
-- 3NF-Normalized | Foreign Keys | Audit Timestamps
-- =============================================================

-- Drop existing tables in reverse dependency order
DROP TABLE IF EXISTS feedback;
DROP TABLE IF EXISTS complaints;
DROP TABLE IF EXISTS departments;
DROP TABLE IF EXISTS users;

-- =============================================================
-- TABLE: users
-- Stores both student and admin accounts (role-differentiated).
-- =============================================================
CREATE TABLE users (
    user_id       INT             NOT NULL AUTO_INCREMENT,
    full_name     VARCHAR(100)    NOT NULL,
    email         VARCHAR(150)    NOT NULL UNIQUE,
    password_hash VARCHAR(255)    NOT NULL,          -- bcrypt / SHA-256 hash
    role          ENUM('student', 'admin')
                                  NOT NULL DEFAULT 'student',
    roll_number   VARCHAR(30)     NULL,              -- applicable to students only
    is_active     TINYINT(1)      NOT NULL DEFAULT 1,
    created_at    TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                           ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (user_id),
    INDEX idx_users_email  (email),
    INDEX idx_users_role   (role)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='Stores student and administrator accounts';


-- =============================================================
-- TABLE: departments
-- Independent lookup table; kept separate (3NF) so department
-- details are not repeated inside every complaint row.
-- =============================================================
CREATE TABLE departments (
    dept_id       INT             NOT NULL AUTO_INCREMENT,
    dept_name     VARCHAR(100)    NOT NULL UNIQUE,   -- e.g. "Hostel", "Electrical", "Academic"
    officer_name  VARCHAR(100)    NULL,
    officer_email VARCHAR(150)    NULL,
    created_at    TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                           ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (dept_id),
    INDEX idx_departments_name (dept_name)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='College departments that complaints are routed to';


-- =============================================================
-- TABLE: complaints
-- Core table; one row per submitted grievance ticket.
-- student_id  → users(user_id)   [the submitting student]
-- assigned_to → users(user_id)   [the admin handling the ticket]
-- dept_id     → departments(dept_id)
-- =============================================================
CREATE TABLE complaints (
    complaint_id    INT             NOT NULL AUTO_INCREMENT,
    ticket_id       VARCHAR(20)     NOT NULL UNIQUE, -- e.g. "CMP-20261003-0042"
    student_id      INT             NOT NULL,
    dept_id         INT             NULL,             -- set after NLP routing
    assigned_to     INT             NULL,             -- admin user_id

    title           VARCHAR(255)    NOT NULL,
    description     TEXT            NOT NULL,
    attachment_path VARCHAR(500)    NULL,             -- relative path to uploaded file

    -- NLP-assigned fields (may be overridden by admin)
    category        VARCHAR(100)    NULL,             -- e.g. "Hostel", "Electrical", "Academic"
    priority        ENUM('low', 'medium', 'high', 'urgent')
                                    NOT NULL DEFAULT 'medium',

    -- Lifecycle status (FR-04 / slide 17)
    status          ENUM('pending', 'in_progress', 'resolved', 'closed')
                                    NOT NULL DEFAULT 'pending',

    resolved_at     TIMESTAMP       NULL,
    closed_at       TIMESTAMP       NULL,
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                             ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (complaint_id),
    INDEX idx_complaints_ticket    (ticket_id),
    INDEX idx_complaints_student   (student_id),
    INDEX idx_complaints_dept      (dept_id),
    INDEX idx_complaints_status    (status),
    INDEX idx_complaints_priority  (priority),

    CONSTRAINT fk_complaints_student
        FOREIGN KEY (student_id)
        REFERENCES users (user_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,

    CONSTRAINT fk_complaints_dept
        FOREIGN KEY (dept_id)
        REFERENCES departments (dept_id)
        ON DELETE SET NULL
        ON UPDATE CASCADE,

    CONSTRAINT fk_complaints_admin
        FOREIGN KEY (assigned_to)
        REFERENCES users (user_id)
        ON DELETE SET NULL
        ON UPDATE CASCADE

) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='Core grievance ticket table';


-- =============================================================
-- TABLE: feedback
-- Post-resolution ratings submitted by the student (slide 17).
-- One feedback row per complaint (enforced by UNIQUE on ticket).
-- =============================================================
CREATE TABLE feedback (
    feedback_id     INT             NOT NULL AUTO_INCREMENT,
    complaint_id    INT             NOT NULL UNIQUE, -- one rating per ticket
    student_id      INT             NOT NULL,
    rating          TINYINT         NOT NULL,        -- 1–5 star scale
    remark          TEXT            NULL,
    submitted_at    TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (feedback_id),
    INDEX idx_feedback_complaint (complaint_id),
    INDEX idx_feedback_student   (student_id),

    CONSTRAINT fk_feedback_complaint
        FOREIGN KEY (complaint_id)
        REFERENCES complaints (complaint_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    CONSTRAINT fk_feedback_student
        FOREIGN KEY (student_id)
        REFERENCES users (user_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,

    CONSTRAINT chk_feedback_rating
        CHECK (rating BETWEEN 1 AND 5)

) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='Post-resolution star ratings from students';


-- =============================================================
-- Seed Data: default departments
-- =============================================================
INSERT INTO departments (dept_name, officer_name, officer_email) VALUES
    ('Hostel',      NULL, NULL),
    ('Electrical',  NULL, NULL),
    ('Academic',    NULL, NULL),
    ('Maintenance', NULL, NULL),
    ('IT Support',  NULL, NULL),
    ('Transport',   NULL, NULL);
