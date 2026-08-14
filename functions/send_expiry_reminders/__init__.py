"""
send_expiry_reminders

Timer-triggered Azure Function. Finds Completions rows whose expiry_date
falls within the warning window and hasn't already been reminded about
(reminder_sent_at IS NULL), sends an email via Resend, then stamps
reminder_sent_at so it isn't sent again.

App settings expected (wired via Key Vault references -- see
infra/modules/functions/main.tf and infra/modules/comms):
    SQL_CONNECTION_STRING
    RESEND_API_KEY
    RESEND_FROM_ADDRESS

Optional:
    EXPIRY_WARNING_DAYS   - how many days out to warn (default 30)
"""

import os
import logging
import pyodbc
import resend
import azure.functions as func

EXPIRY_WARNING_DAYS = int(os.environ.get("EXPIRY_WARNING_DAYS", "30"))

QUERY = """
    SELECT
        c.id            AS completion_id,
        e.email,
        e.name          AS employee_name,
        co.title        AS course_title,
        c.expiry_date,
        comp.name       AS company_name
    FROM Completions c
    JOIN Employees e ON e.id = c.employee_id
    JOIN Courses co  ON co.id = c.course_id
    JOIN Companies comp ON comp.id = e.company_id
    WHERE c.status = 'completed'
      AND c.expiry_date IS NOT NULL
      AND c.expiry_date BETWEEN CAST(GETDATE() AS DATE)
          AND DATEADD(DAY, ?, CAST(GETDATE() AS DATE))
      AND c.reminder_sent_at IS NULL
"""

MARK_SENT = """
    UPDATE Completions
    SET reminder_sent_at = SYSUTCDATETIME()
    WHERE id = ?
"""


def get_db_connection():
    return pyodbc.connect(os.environ["SQL_CONNECTION_STRING"])


def send_reminder_email(to_email: str, employee_name: str, course_title: str,
                         expiry_date, company_name: str) -> None:
    resend.api_key = os.environ["RESEND_API_KEY"]
    from_address = os.environ["RESEND_FROM_ADDRESS"]

    resend.Emails.send({
        "from": f"{company_name} Training <{from_address}>",
        "to": [to_email],
        "subject": f"Action needed: {course_title} expires soon",
        "html": (
            f"<p>Hi {employee_name},</p>"
            f"<p>Your completion of <strong>{course_title}</strong> is set to expire on "
            f"<strong>{expiry_date.strftime('%B %d, %Y')}</strong>.</p>"
            f"<p>Please log in to the training portal to retake it before the deadline.</p>"
        ),
    })


def main(mytimer: func.TimerRequest) -> None:
    logging.info("send_expiry_reminders: starting run")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(QUERY, EXPIRY_WARNING_DAYS)
    rows = cursor.fetchall()

    sent_count = 0
    failed_count = 0

    for row in rows:
        completion_id, email, employee_name, course_title, expiry_date, company_name = row
        try:
            send_reminder_email(email, employee_name, course_title, expiry_date, company_name)

            mark_cursor = conn.cursor()
            mark_cursor.execute(MARK_SENT, completion_id)
            conn.commit()

            sent_count += 1
        except Exception as exc:
            failed_count += 1
            logging.error(f"Failed to send reminder for completion {completion_id} ({email}): {exc}")

    conn.close()
    logging.info(f"send_expiry_reminders: done — sent={sent_count} failed={failed_count}")
