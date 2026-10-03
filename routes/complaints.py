"""
routes/complaints.py
--------------------
Student-facing complaint Blueprint for the Smart College Complaint
Management System.

Endpoints
---------
GET  /complaints/submit          → render submission form
POST /complaints/submit          → validate, NLP-triage, insert, show ticket ID
GET  /complaints/track           → render ticket lookup form
POST /complaints/track           → look up a complaint by ticket ID
GET  /complaints/history         → list all complaints for the logged-in student
GET  /complaints/<ticket_id>     → detail view for a single complaint
POST /complaints/<ticket_id>/feedback → submit star rating after resolution

NLP integration
---------------
On every POST /complaints/submit the description is passed through
nlp_engine.engine.predict() which returns (category, priority).
The admin can override both fields later from the admin dashboard
(Phase 4).

Security
--------
* All routes require @student_required (login + role check).
* File uploads are validated by extension and renamed to a UUID
  so that path-traversal payloads can never be executed.
* Every SQL statement uses %s parameterised queries.
"""

import os
import logging
import uuid
from datetime import datetime, timezone
from werkzeug.utils import secure_filename

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    current_app,
    jsonify,
)

from app import get_db, generate_ticket_id, allowed_file
from routes.auth import student_required
from nlp_engine import engine as nlp_engine

logger = logging.getLogger(__name__)

complaints_bp = Blueprint("complaints", __name__)


# ---------------------------------------------------------------------------
# Lazy-load the NLP model once per process
# ---------------------------------------------------------------------------
def _ensure_nlp_loaded() -> bool:
    """
    Load the NLP model on first use.
    Returns True if the model is available, False if the model file is
    missing (e.g. nlp_trainer.py has not been run yet).
    """
    if not nlp_engine.is_loaded:
        try:
            nlp_engine.load()
        except FileNotFoundError:
            logger.warning("NLP model not found — predictions will use fallback values.")
            return False
    return True


# ---------------------------------------------------------------------------
# Complaint Submission  (FR-02, FR-03)
# ---------------------------------------------------------------------------
@complaints_bp.route("/submit", methods=["GET", "POST"])
@student_required
def submit():
    """
    GET  → render the complaint submission form.
    POST → validate → NLP triage → generate ticket ID → insert to DB.
    """
    if request.method == "GET":
        return render_template("complaints/submit.html")

    # ── Collect form data ─────────────────────────────────────────────
    title       = request.form.get("title",       "").strip()
    description = request.form.get("description", "").strip()
    # Optional manual category hint (student can leave as 'Auto-Detect')
    manual_cat  = request.form.get("category",    "").strip()

    # ── Validation ───────────────────────────────────────────────────
    errors = []
    if not title:
        errors.append("Complaint title is required.")
    if len(title) > 255:
        errors.append("Title must be under 255 characters.")
    if not description:
        errors.append("Please describe your complaint.")
    if len(description) < 20:
        errors.append("Description must be at least 20 characters.")

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("complaints/submit.html",
                               title=title, description=description)

    # ── NLP Triage ───────────────────────────────────────────────────
    nlp_available = _ensure_nlp_loaded()
    if nlp_available:
        nlp_category, nlp_priority = nlp_engine.predict(description)
    else:
        nlp_category, nlp_priority = "Academic", "medium"   # safe fallback

    # Student manual override takes precedence if explicitly chosen
    final_category = manual_cat if manual_cat and manual_cat != "auto" else nlp_category

    # ── File upload (optional) ───────────────────────────────────────
    attachment_path = None
    upload_file = request.files.get("attachment")
    if upload_file and upload_file.filename:
        original_name = secure_filename(upload_file.filename)
        if not allowed_file(original_name):
            flash("Invalid file type. Allowed: pdf, png, jpg, jpeg, gif, docx.", "danger")
            return render_template("complaints/submit.html",
                                   title=title, description=description)
        ext           = original_name.rsplit(".", 1)[1].lower()
        safe_filename = f"{uuid.uuid4().hex}.{ext}"    # UUID rename — no traversal risk
        save_path     = os.path.join(current_app.config["UPLOAD_FOLDER"], safe_filename)
        upload_file.save(save_path)
        attachment_path = safe_filename
        logger.info("Attachment saved | filename=%s", safe_filename)

    # ── Generate unique ticket ID ────────────────────────────────────
    ticket_id = generate_ticket_id()

    # ── Database insert ──────────────────────────────────────────────
    db = get_db()
    try:
        with db.cursor() as cur:
            # Resolve dept_id from category name (may be NULL if unknown)
            cur.execute(
                "SELECT dept_id FROM departments WHERE dept_name = %s LIMIT 1",
                (final_category,)
            )
            dept_row = cur.fetchone()
            dept_id  = dept_row["dept_id"] if dept_row else None

            cur.execute(
                """
                INSERT INTO complaints
                    (ticket_id, student_id, dept_id, title, description,
                     attachment_path, category, priority, status)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    ticket_id,
                    session["user_id"],
                    dept_id,
                    title,
                    description,
                    attachment_path,
                    final_category,
                    nlp_priority,
                )
            )
            db.commit()

        logger.info("Complaint submitted | ticket=%s student=%s cat=%s priority=%s",
                    ticket_id, session["user_id"], final_category, nlp_priority)

        flash(
            f"Complaint submitted successfully! "
            f"Your Ticket ID is: {ticket_id}",
            "success"
        )
        return redirect(url_for("complaints.detail", ticket_id=ticket_id))

    except Exception as exc:
        db.rollback()
        logger.error("Complaint insert failed | student=%s | %s",
                     session.get("user_id"), exc)
        flash("Submission failed due to a server error. Please try again.", "danger")
        return render_template("complaints/submit.html",
                               title=title, description=description)


# ---------------------------------------------------------------------------
# Ticket Tracking  (FR-03)
# ---------------------------------------------------------------------------
@complaints_bp.route("/track", methods=["GET", "POST"])
@student_required
def track():
    """
    GET  → render the ticket lookup form.
    POST → query DB by ticket_id and show status to the student.
    """
    complaint = None

    if request.method == "POST":
        ticket_id = request.form.get("ticket_id", "").strip().upper()

        if not ticket_id:
            flash("Please enter a Ticket ID.", "warning")
            return render_template("complaints/track.html")

        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """
                SELECT c.ticket_id, c.title, c.category, c.priority,
                       c.status, c.created_at, c.updated_at,
                       d.dept_name,
                       u.full_name AS assigned_admin
                FROM   complaints c
                LEFT JOIN departments d ON c.dept_id    = d.dept_id
                LEFT JOIN users      u ON c.assigned_to = u.user_id
                WHERE  c.ticket_id  = %s
                AND    c.student_id = %s
                LIMIT  1
                """,
                (ticket_id, session["user_id"])
            )
            complaint = cur.fetchone()

        if not complaint:
            flash("No complaint found with that Ticket ID for your account.", "warning")

    return render_template("complaints/track.html", complaint=complaint)


# ---------------------------------------------------------------------------
# Complaint History  (student dashboard list)
# ---------------------------------------------------------------------------
@complaints_bp.route("/history")
@student_required
def history():
    """
    Display all complaints filed by the currently logged-in student,
    newest first.
    """
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT c.ticket_id, c.title, c.category, c.priority,
                   c.status, c.created_at,
                   d.dept_name
            FROM   complaints c
            LEFT JOIN departments d ON c.dept_id = d.dept_id
            WHERE  c.student_id = %s
            ORDER  BY c.created_at DESC
            """,
            (session["user_id"],)
        )
        complaints = cur.fetchall()

    return render_template("complaints/history.html", complaints=complaints)


# ---------------------------------------------------------------------------
# Complaint Detail
# ---------------------------------------------------------------------------
@complaints_bp.route("/<ticket_id>")
@student_required
def detail(ticket_id: str):
    """
    Show the full detail of a single complaint.
    Students can only view their own complaints.
    """
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT c.*,
                   d.dept_name,
                   u.full_name AS assigned_admin
            FROM   complaints c
            LEFT JOIN departments d ON c.dept_id    = d.dept_id
            LEFT JOIN users      u ON c.assigned_to = u.user_id
            WHERE  c.ticket_id  = %s
            AND    c.student_id = %s
            LIMIT  1
            """,
            (ticket_id.upper(), session["user_id"])
        )
        complaint = cur.fetchone()

    if not complaint:
        flash("Complaint not found or access denied.", "warning")
        return redirect(url_for("complaints.history"))

    # Fetch existing feedback if already submitted
    feedback = None
    if complaint["status"] in ("resolved", "closed"):
        with db.cursor() as cur:
            cur.execute(
                """
                SELECT rating, remark, submitted_at
                FROM   feedback
                WHERE  complaint_id = %s
                LIMIT  1
                """,
                (complaint["complaint_id"],)
            )
            feedback = cur.fetchone()

    return render_template("complaints/detail.html",
                           complaint=complaint,
                           feedback=feedback)


# ---------------------------------------------------------------------------
# Feedback / Rating  (post-resolution)
# ---------------------------------------------------------------------------
@complaints_bp.route("/<ticket_id>/feedback", methods=["POST"])
@student_required
def submit_feedback(ticket_id: str):
    """
    Accept a 1–5 star rating and optional remark after a complaint
    is resolved or closed.  One feedback row per complaint (enforced
    by UNIQUE constraint in schema).
    """
    rating_raw = request.form.get("rating", "").strip()
    remark     = request.form.get("remark", "").strip()

    # Validate rating
    try:
        rating = int(rating_raw)
        if not 1 <= rating <= 5:
            raise ValueError
    except ValueError:
        flash("Please select a rating between 1 and 5.", "danger")
        return redirect(url_for("complaints.detail", ticket_id=ticket_id))

    db = get_db()
    try:
        with db.cursor() as cur:
            # Verify complaint belongs to this student and is resolved/closed
            cur.execute(
                """
                SELECT complaint_id, status
                FROM   complaints
                WHERE  ticket_id  = %s
                AND    student_id = %s
                LIMIT  1
                """,
                (ticket_id.upper(), session["user_id"])
            )
            row = cur.fetchone()

        if not row:
            flash("Complaint not found.", "warning")
            return redirect(url_for("complaints.history"))

        if row["status"] not in ("resolved", "closed"):
            flash("You can only rate a complaint after it has been resolved.", "warning")
            return redirect(url_for("complaints.detail", ticket_id=ticket_id))

        with db.cursor() as cur:
            # INSERT IGNORE handles the UNIQUE constraint gracefully
            cur.execute(
                """
                INSERT IGNORE INTO feedback
                    (complaint_id, student_id, rating, remark)
                VALUES
                    (%s, %s, %s, %s)
                """,
                (row["complaint_id"], session["user_id"], rating, remark or None)
            )
            db.commit()

        flash("Thank you for your feedback!", "success")

    except Exception as exc:
        db.rollback()
        logger.error("Feedback insert failed | ticket=%s | %s", ticket_id, exc)
        flash("Could not save feedback. Please try again.", "danger")

    return redirect(url_for("complaints.detail", ticket_id=ticket_id))


# ---------------------------------------------------------------------------
# AJAX endpoint — live NLP preview (called from the submit form via JS)
# ---------------------------------------------------------------------------
@complaints_bp.route("/api/preview-triage", methods=["POST"])
@student_required
def preview_triage():
    """
    Accept a JSON body {"description": "..."} and return the predicted
    category + priority so the form can show a live preview to the
    student before they submit.

    Returns
    -------
    JSON: {"category": str, "priority": str, "scores": dict}
    """
    data = request.get_json(silent=True) or {}
    text = data.get("description", "").strip()

    if len(text) < 10:
        return jsonify({"category": "—", "priority": "—", "scores": {}})

    nlp_available = _ensure_nlp_loaded()
    if not nlp_available:
        return jsonify({"category": "Academic", "priority": "medium", "scores": {}})

    category, priority = nlp_engine.predict(text)
    scores = nlp_engine.predict_proba(text)
    return jsonify({"category": category, "priority": priority, "scores": scores})
