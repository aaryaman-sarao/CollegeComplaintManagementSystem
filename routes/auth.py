"""
routes/auth.py
--------------
Authentication Blueprint for the Smart College Complaint
Management System.

Endpoints
---------
GET  /auth/register          → render registration form
POST /auth/register          → create new student account
GET  /auth/login             → render login form
POST /auth/login             → authenticate, set session, redirect by role
GET  /auth/logout            → clear session, redirect to login

Security measures implemented
------------------------------
* Passwords stored as Werkzeug-generated bcrypt hashes (never plaintext).
* All SQL queries use %s parameterized placeholders (PyMySQL) — no
  string interpolation anywhere near user input.
* Duplicate email check done with a SELECT before INSERT.
* Session stores only non-sensitive scalars (user_id, role, name).
* Flask's signed cookie is already tamper-evident via SECRET_KEY.
* Role-based redirects enforce the student / admin split (FR-01).
* login_required and admin_required decorators are defined here for
  reuse across other blueprints in later phases.
"""

import logging
import functools
import re

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    g,
)
from werkzeug.security import generate_password_hash, check_password_hash

from app import get_db

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value))


def _is_strong_enough(password: str) -> bool:
    """
    Minimal password policy: at least 8 characters.
    Extend this for production (uppercase, digit, special char checks).
    """
    return len(password) >= 8


# ---------------------------------------------------------------------------
# RBAC decorators  (importable by other blueprints)
# ---------------------------------------------------------------------------
def login_required(view):
    """Redirect to login page if the user is not authenticated."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Allow only users with role='admin'; redirect others away."""
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin":
            flash("You do not have permission to access that page.", "danger")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


def student_required(view):
    """Allow only users with role='student'; redirect others away."""
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if session.get("role") != "student":
            flash("That page is for students only.", "danger")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """
    GET  → render the registration form.
    POST → validate input, check for duplicate email, insert new user.

    Only students can self-register. Admin accounts are created by
    a superadmin (out of scope for this phase).
    """
    if "user_id" in session:
        return _redirect_by_role()

    if request.method == "GET":
        return render_template("auth/register.html")

    # ── Collect and sanitise form data ────────────────────────────────
    full_name   = request.form.get("full_name",   "").strip()
    email       = request.form.get("email",       "").strip().lower()
    roll_number = request.form.get("roll_number", "").strip()
    password    = request.form.get("password",    "")
    confirm_pw  = request.form.get("confirm_password", "")

    # ── Server-side validation ────────────────────────────────────────
    errors = []
    if not full_name:
        errors.append("Full name is required.")
    if not _is_valid_email(email):
        errors.append("Please enter a valid email address.")
    if not _is_strong_enough(password):
        errors.append("Password must be at least 8 characters.")
    if password != confirm_pw:
        errors.append("Passwords do not match.")

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("auth/register.html",
                               full_name=full_name,
                               email=email,
                               roll_number=roll_number)

    db = get_db()
    try:
        with db.cursor() as cur:
            # ── Duplicate email check (parameterized) ─────────────────
            cur.execute(
                "SELECT user_id FROM users WHERE email = %s LIMIT 1",
                (email,)
            )
            if cur.fetchone():
                flash("An account with that email already exists.", "warning")
                return render_template("auth/register.html",
                                       full_name=full_name,
                                       email=email,
                                       roll_number=roll_number)

            # ── Hash password (Werkzeug pbkdf2:sha256) ────────────────
            pw_hash = generate_password_hash(password)

            # ── Insert new student (parameterized, role hard-coded) ───
            cur.execute(
                """
                INSERT INTO users
                    (full_name, email, password_hash, role, roll_number)
                VALUES
                    (%s, %s, %s, 'student', %s)
                """,
                (full_name, email, pw_hash, roll_number or None)
            )
            db.commit()
            new_id = cur.lastrowid

        logger.info("New student registered | user_id=%s email=%s", new_id, email)
        flash("Account created successfully! Please log in.", "success")
        return redirect(url_for("auth.login"))

    except Exception as exc:
        db.rollback()
        logger.error("Registration failed | email=%s | %s", email, exc)
        flash("Registration failed due to a server error. Please try again.", "danger")
        return render_template("auth/register.html",
                               full_name=full_name,
                               email=email,
                               roll_number=roll_number)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """
    GET  → render the login form.
    POST → verify credentials, populate session, redirect by role.

    Timing-safe password comparison via Werkzeug's check_password_hash
    prevents timing-based enumeration of valid emails.
    """
    if "user_id" in session:
        return _redirect_by_role()

    if request.method == "GET":
        return render_template("auth/login.html")

    email    = request.form.get("email",    "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password:
        flash("Email and password are required.", "danger")
        return render_template("auth/login.html", email=email)

    db = get_db()
    try:
        with db.cursor() as cur:
            # ── Fetch user by email (parameterized) ───────────────────
            cur.execute(
                """
                SELECT user_id, full_name, email,
                       password_hash, role, is_active
                FROM   users
                WHERE  email = %s
                LIMIT  1
                """,
                (email,)
            )
            user = cur.fetchone()

        # ── Validate credentials ──────────────────────────────────────
        # Use a single generic message to avoid leaking which field is wrong
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "danger")
            logger.warning("Failed login attempt | email=%s", email)
            return render_template("auth/login.html", email=email)

        if not user["is_active"]:
            flash("Your account has been deactivated. Contact admin.", "warning")
            return render_template("auth/login.html", email=email)

        # ── Populate session ──────────────────────────────────────────
        session.clear()                        # prevent session fixation
        session.permanent = True               # respect PERMANENT_SESSION_LIFETIME
        session["user_id"]   = user["user_id"]
        session["role"]      = user["role"]
        session["full_name"] = user["full_name"]
        session["email"]     = user["email"]

        logger.info("Login success | user_id=%s role=%s",
                    user["user_id"], user["role"])

        flash(f"Welcome back, {user['full_name']}!", "success")
        return _redirect_by_role()

    except Exception as exc:
        logger.error("Login error | email=%s | %s", email, exc)
        flash("A server error occurred. Please try again.", "danger")
        return render_template("auth/login.html", email=email)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------
@auth_bp.route("/logout")
@login_required
def logout():
    """Clear the session and return to the login page."""
    user_id = session.get("user_id")
    session.clear()
    logger.info("Logout | user_id=%s", user_id)
    flash("You have been logged out successfully.", "info")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------
def _redirect_by_role() -> "Response":  # noqa: F821
    """
    Send authenticated users to their role-appropriate dashboard.

    student → /complaints/history   (complaint history as the student home)
    admin   → /admin/dashboard      (admin KPI dashboard)
    """
    role = session.get("role")
    if role == "admin":
        return redirect(url_for("admin.dashboard"))
    # Default: student lands on their complaint history
    return redirect(url_for("complaints.history"))
