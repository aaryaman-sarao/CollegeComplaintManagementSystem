"""
routes/admin.py
---------------
Admin Blueprint for the Smart College Complaint Management System.

Endpoints
---------
GET  /admin/dashboard                   → summary stats cards
GET  /admin/complaints                  → paginated list with filters
GET  /admin/complaints/<ticket_id>      → full complaint detail
POST /admin/complaints/<ticket_id>/update  → status + priority override
POST /admin/complaints/<ticket_id>/assign  → re-assign to dept + admin
GET  /admin/analytics                   → resolution metrics & charts data
GET  /admin/analytics/data              → JSON feed for Chart.js
GET  /admin/users                       → list all student accounts
POST /admin/users/<user_id>/toggle      → activate / deactivate a student

Security
--------
* Every route requires @admin_required (login + role='admin').
* All SQL uses %s parameterised placeholders — no string interpolation.
* Status transitions are validated server-side against the allowed
  lifecycle (pending → in_progress → resolved → closed).
"""

import logging
from datetime import datetime, timezone

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
)

from app import get_db
from routes.auth import admin_required

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_STATUSES   = ("pending", "in_progress", "resolved", "closed")
VALID_PRIORITIES = ("low", "medium", "high", "urgent")

# Allowed forward transitions  state → set of reachable next states
STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending":     {"in_progress", "closed"},
    "in_progress": {"resolved",    "closed"},
    "resolved":    {"closed"},
    "closed":      set(),           # terminal — no further movement
}

PAGE_SIZE = 15   # complaints per page


# ---------------------------------------------------------------------------
# Dashboard  (summary stat cards)
# ---------------------------------------------------------------------------
@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    """
    Render the admin landing page with four KPI cards:
        Total open · Pending · In-Progress · Resolved today
    and a mini breakdown by department.
    """
    db = get_db()
    with db.cursor() as cur:

        # ── Overall counts ────────────────────────────────────────────
        cur.execute(
            """
            SELECT
                COUNT(*)                                         AS total,
                SUM(status = 'pending')                          AS pending,
                SUM(status = 'in_progress')                      AS in_progress,
                SUM(status = 'resolved')                         AS resolved,
                SUM(status = 'closed')                           AS closed,
                SUM(priority = 'urgent' AND status != 'closed')  AS urgent_open
            FROM complaints
            """
        )
        stats = cur.fetchone()

        # ── Resolved today ────────────────────────────────────────────
        cur.execute(
            """
            SELECT COUNT(*) AS resolved_today
            FROM   complaints
            WHERE  status      = 'resolved'
            AND    DATE(resolved_at) = CURDATE()
            """
        )
        today = cur.fetchone()
        stats["resolved_today"] = today["resolved_today"]

        # ── Per-department open count ─────────────────────────────────
        cur.execute(
            """
            SELECT   d.dept_name,
                     COUNT(c.complaint_id)          AS open_count,
                     SUM(c.priority = 'urgent')     AS urgent_count
            FROM     departments d
            LEFT JOIN complaints c
                   ON c.dept_id = d.dept_id
                  AND c.status NOT IN ('closed')
            GROUP BY d.dept_id, d.dept_name
            ORDER BY open_count DESC
            """
        )
        dept_stats = cur.fetchall()

        # ── 5 most recent unattended urgent tickets ───────────────────
        cur.execute(
            """
            SELECT ticket_id, title, category, created_at
            FROM   complaints
            WHERE  priority = 'urgent'
            AND    status   = 'pending'
            ORDER  BY created_at ASC
            LIMIT  5
            """
        )
        urgent_queue = cur.fetchall()

    return render_template(
        "admin/dashboard.html",
        stats=stats,
        dept_stats=dept_stats,
        urgent_queue=urgent_queue,
    )


# ---------------------------------------------------------------------------
# Complaint List  (paginated + filtered)
# ---------------------------------------------------------------------------
@admin_bp.route("/complaints")
@admin_required
def complaint_list():
    """
    Paginated complaint list.

    Query-string filters
    --------------------
    status    : pending | in_progress | resolved | closed | (blank = all)
    priority  : low | medium | high | urgent      | (blank = all)
    dept_id   : integer dept_id                   | (blank = all)
    q         : free-text search on title / ticket_id
    page      : page number (default 1)
    """
    status_filter   = request.args.get("status",   "").strip()
    priority_filter = request.args.get("priority", "").strip()
    dept_filter     = request.args.get("dept_id",  "").strip()
    search_q        = request.args.get("q",        "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    offset = (page - 1) * PAGE_SIZE

    # ── Build WHERE clause dynamically (values via params list) ──────
    where_clauses = []
    params: list = []

    if status_filter and status_filter in VALID_STATUSES:
        where_clauses.append("c.status = %s")
        params.append(status_filter)

    if priority_filter and priority_filter in VALID_PRIORITIES:
        where_clauses.append("c.priority = %s")
        params.append(priority_filter)

    if dept_filter:
        where_clauses.append("c.dept_id = %s")
        params.append(dept_filter)

    if search_q:
        where_clauses.append("(c.ticket_id LIKE %s OR c.title LIKE %s)")
        like = f"%{search_q}%"
        params.extend([like, like])

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    db = get_db()
    with db.cursor() as cur:
        # Total count for pagination
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM complaints c {where_sql}",
            params
        )
        total = cur.fetchone()["cnt"]

        # Paginated rows
        cur.execute(
            f"""
            SELECT  c.ticket_id, c.title, c.category, c.priority,
                    c.status,   c.created_at, c.updated_at,
                    d.dept_name,
                    u.full_name  AS student_name,
                    a.full_name  AS assigned_admin
            FROM    complaints c
            LEFT JOIN departments d ON c.dept_id    = d.dept_id
            LEFT JOIN users       u ON c.student_id = u.user_id
            LEFT JOIN users       a ON c.assigned_to = a.user_id
            {where_sql}
            ORDER   BY
                FIELD(c.priority, 'urgent','high','medium','low'),
                c.created_at ASC
            LIMIT  %s OFFSET %s
            """,
            params + [PAGE_SIZE, offset]
        )
        complaints = cur.fetchall()

        # Department list for filter dropdown
        cur.execute("SELECT dept_id, dept_name FROM departments ORDER BY dept_name")
        departments = cur.fetchall()

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return render_template(
        "admin/complaint_list.html",
        complaints=complaints,
        departments=departments,
        # preserve current filter values for the form
        status_filter=status_filter,
        priority_filter=priority_filter,
        dept_filter=dept_filter,
        search_q=search_q,
        page=page,
        total_pages=total_pages,
        total=total,
    )


# ---------------------------------------------------------------------------
# Complaint Detail
# ---------------------------------------------------------------------------
@admin_bp.route("/complaints/<ticket_id>")
@admin_required
def complaint_detail(ticket_id: str):
    """Full detail view for a single complaint, with action panels."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT  c.*,
                    d.dept_name,
                    u.full_name  AS student_name,
                    u.email      AS student_email,
                    u.roll_number,
                    a.full_name  AS assigned_admin_name
            FROM    complaints c
            LEFT JOIN departments d ON c.dept_id     = d.dept_id
            LEFT JOIN users       u ON c.student_id  = u.user_id
            LEFT JOIN users       a ON c.assigned_to = a.user_id
            WHERE   c.ticket_id = %s
            LIMIT   1
            """,
            (ticket_id.upper(),)
        )
        complaint = cur.fetchone()

    if not complaint:
        flash("Complaint not found.", "warning")
        return redirect(url_for("admin.complaint_list"))

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT dept_id, dept_name FROM departments ORDER BY dept_name"
        )
        departments = cur.fetchall()

        # Admin users for assignment dropdown
        cur.execute(
            """
            SELECT user_id, full_name
            FROM   users
            WHERE  role = 'admin' AND is_active = 1
            ORDER  BY full_name
            """
        )
        admins = cur.fetchall()

        # Feedback if any
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

    # Compute allowed next statuses for the action panel
    current_status    = complaint["status"]
    allowed_next      = sorted(STATUS_TRANSITIONS.get(current_status, set()))

    return render_template(
        "admin/complaint_detail.html",
        complaint=complaint,
        departments=departments,
        admins=admins,
        feedback=feedback,
        allowed_next=allowed_next,
        valid_priorities=VALID_PRIORITIES,
    )


# ---------------------------------------------------------------------------
# Update Status + Priority  (FR-04 / slide 16)
# ---------------------------------------------------------------------------
@admin_bp.route("/complaints/<ticket_id>/update", methods=["POST"])
@admin_required
def update_complaint(ticket_id: str):
    """
    Override the status and/or priority of a complaint.
    Validates that the requested status transition is permitted.
    """
    new_status   = request.form.get("status",   "").strip()
    new_priority = request.form.get("priority", "").strip()

    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute(
                "SELECT complaint_id, status FROM complaints WHERE ticket_id = %s LIMIT 1",
                (ticket_id.upper(),)
            )
            row = cur.fetchone()

        if not row:
            flash("Complaint not found.", "warning")
            return redirect(url_for("admin.complaint_list"))

        current_status = row["status"]
        updates        = []
        params: list   = []

        # ── Validate and apply status change ─────────────────────────
        if new_status and new_status != current_status:
            if new_status not in VALID_STATUSES:
                flash(f"'{new_status}' is not a valid status.", "danger")
                return redirect(url_for("admin.complaint_detail",
                                        ticket_id=ticket_id))

            if new_status not in STATUS_TRANSITIONS.get(current_status, set()):
                flash(
                    f"Cannot move a '{current_status}' complaint "
                    f"directly to '{new_status}'.",
                    "danger"
                )
                return redirect(url_for("admin.complaint_detail",
                                        ticket_id=ticket_id))

            updates.append("status = %s")
            params.append(new_status)

            # Stamp resolved_at / closed_at automatically
            now = datetime.now(timezone.utc)
            if new_status == "resolved":
                updates.append("resolved_at = %s")
                params.append(now)
            elif new_status == "closed":
                updates.append("closed_at = %s")
                params.append(now)

        # ── Validate and apply priority override ─────────────────────
        if new_priority and new_priority in VALID_PRIORITIES:
            updates.append("priority = %s")
            params.append(new_priority)

        if not updates:
            flash("No changes to apply.", "info")
            return redirect(url_for("admin.complaint_detail",
                                    ticket_id=ticket_id))

        params.append(ticket_id.upper())
        with db.cursor() as cur:
            cur.execute(
                f"UPDATE complaints SET {', '.join(updates)} WHERE ticket_id = %s",
                params
            )
            db.commit()

        logger.info("Complaint updated | ticket=%s admin=%s status=%s priority=%s",
                    ticket_id, session.get("user_id"), new_status, new_priority)
        flash("Complaint updated successfully.", "success")

    except Exception as exc:
        db.rollback()
        logger.error("Update failed | ticket=%s | %s", ticket_id, exc)
        flash("Update failed due to a server error.", "danger")

    return redirect(url_for("admin.complaint_detail", ticket_id=ticket_id))


# ---------------------------------------------------------------------------
# Re-assign Department + Admin
# ---------------------------------------------------------------------------
@admin_bp.route("/complaints/<ticket_id>/assign", methods=["POST"])
@admin_required
def assign_complaint(ticket_id: str):
    """
    Re-route a complaint to a different department and optionally
    assign it to a specific admin user.
    """
    dept_id_raw  = request.form.get("dept_id",     "").strip()
    admin_id_raw = request.form.get("assigned_to", "").strip()

    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute(
                "SELECT complaint_id FROM complaints WHERE ticket_id = %s LIMIT 1",
                (ticket_id.upper(),)
            )
            if not cur.fetchone():
                flash("Complaint not found.", "warning")
                return redirect(url_for("admin.complaint_list"))

            updates = []
            params: list = []

            # ── Dept validation ───────────────────────────────────────
            if dept_id_raw:
                cur.execute(
                    "SELECT dept_id FROM departments WHERE dept_id = %s LIMIT 1",
                    (dept_id_raw,)
                )
                if not cur.fetchone():
                    flash("Invalid department selected.", "danger")
                    return redirect(url_for("admin.complaint_detail",
                                            ticket_id=ticket_id))
                updates.append("dept_id = %s")
                params.append(dept_id_raw)

            # ── Admin assignment validation ───────────────────────────
            if admin_id_raw:
                cur.execute(
                    """
                    SELECT user_id FROM users
                    WHERE  user_id = %s AND role = 'admin' AND is_active = 1
                    LIMIT  1
                    """,
                    (admin_id_raw,)
                )
                if not cur.fetchone():
                    flash("Invalid admin user selected.", "danger")
                    return redirect(url_for("admin.complaint_detail",
                                            ticket_id=ticket_id))
                updates.append("assigned_to = %s")
                params.append(admin_id_raw)

            if not updates:
                flash("No assignment changes to apply.", "info")
                return redirect(url_for("admin.complaint_detail",
                                        ticket_id=ticket_id))

            # Bump status to in_progress if still pending
            updates.append(
                "status = CASE WHEN status = 'pending' THEN 'in_progress' ELSE status END"
            )

            params.append(ticket_id.upper())
            cur.execute(
                f"UPDATE complaints SET {', '.join(updates)} WHERE ticket_id = %s",
                params
            )
            db.commit()

        logger.info("Complaint assigned | ticket=%s dept=%s admin=%s by=%s",
                    ticket_id, dept_id_raw, admin_id_raw, session.get("user_id"))
        flash("Complaint re-assigned successfully.", "success")

    except Exception as exc:
        db.rollback()
        logger.error("Assign failed | ticket=%s | %s", ticket_id, exc)
        flash("Assignment failed due to a server error.", "danger")

    return redirect(url_for("admin.complaint_detail", ticket_id=ticket_id))


# ---------------------------------------------------------------------------
# Analytics Report  (slide 16 — resolution efficiency)
# ---------------------------------------------------------------------------
@admin_bp.route("/analytics")
@admin_required
def analytics():
    """Render the analytics page shell; data fetched via /analytics/data."""
    db = get_db()
    with db.cursor() as cur:
        # Overall resolution rate
        cur.execute(
            """
            SELECT
                COUNT(*)                                          AS total,
                SUM(status IN ('resolved','closed'))             AS resolved_total,
                ROUND(AVG(
                    CASE WHEN resolved_at IS NOT NULL
                    THEN TIMESTAMPDIFF(HOUR, created_at, resolved_at)
                    END
                ), 1)                                            AS avg_resolution_hrs
            FROM complaints
            """
        )
        overall = cur.fetchone()

        # Per-department resolution stats
        cur.execute(
            """
            SELECT
                d.dept_name,
                COUNT(c.complaint_id)                            AS total,
                SUM(c.status IN ('resolved','closed'))           AS resolved,
                ROUND(AVG(
                    CASE WHEN c.resolved_at IS NOT NULL
                    THEN TIMESTAMPDIFF(HOUR, c.created_at, c.resolved_at)
                    END
                ), 1)                                            AS avg_hrs
            FROM   departments d
            LEFT JOIN complaints c ON c.dept_id = d.dept_id
            GROUP  BY d.dept_id, d.dept_name
            ORDER  BY total DESC
            """
        )
        dept_analytics = cur.fetchall()

        # Priority distribution
        cur.execute(
            """
            SELECT priority, COUNT(*) AS cnt
            FROM   complaints
            GROUP  BY priority
            ORDER  BY FIELD(priority,'urgent','high','medium','low')
            """
        )
        priority_dist = cur.fetchall()

        # Monthly complaint volume (last 6 months)
        cur.execute(
            """
            SELECT
                DATE_FORMAT(created_at, '%Y-%m') AS month,
                COUNT(*)                          AS cnt
            FROM   complaints
            WHERE  created_at >= DATE_SUB(NOW(), INTERVAL 6 MONTH)
            GROUP  BY month
            ORDER  BY month ASC
            """
        )
        monthly_volume = cur.fetchall()

    return render_template(
        "admin/analytics.html",
        overall=overall,
        dept_analytics=dept_analytics,
        priority_dist=priority_dist,
        monthly_volume=monthly_volume,
    )


@admin_bp.route("/analytics/data")
@admin_required
def analytics_data():
    """
    JSON endpoint consumed by Chart.js on the analytics page.

    Returns
    -------
    {
      "status_counts":   { pending: n, in_progress: n, resolved: n, closed: n },
      "priority_counts": { low: n, medium: n, high: n, urgent: n },
      "dept_series":     [ {dept, total, resolved, avg_hrs}, ... ],
      "monthly_volume":  [ {month, cnt}, ... ]
    }
    """
    db = get_db()
    with db.cursor() as cur:

        cur.execute(
            """
            SELECT status, COUNT(*) AS cnt
            FROM   complaints
            GROUP  BY status
            """
        )
        raw_status = cur.fetchall()
        status_counts = {r["status"]: r["cnt"] for r in raw_status}
        # ensure all keys present
        for s in VALID_STATUSES:
            status_counts.setdefault(s, 0)

        cur.execute(
            """
            SELECT priority, COUNT(*) AS cnt
            FROM   complaints
            GROUP  BY priority
            """
        )
        raw_priority = cur.fetchall()
        priority_counts = {r["priority"]: r["cnt"] for r in raw_priority}
        for p in VALID_PRIORITIES:
            priority_counts.setdefault(p, 0)

        cur.execute(
            """
            SELECT
                d.dept_name                                          AS dept,
                COUNT(c.complaint_id)                                AS total,
                SUM(c.status IN ('resolved','closed'))               AS resolved,
                COALESCE(ROUND(AVG(
                    CASE WHEN c.resolved_at IS NOT NULL
                    THEN TIMESTAMPDIFF(HOUR, c.created_at, c.resolved_at)
                    END
                ), 1), 0)                                            AS avg_hrs
            FROM   departments d
            LEFT JOIN complaints c ON c.dept_id = d.dept_id
            GROUP  BY d.dept_id, d.dept_name
            ORDER  BY total DESC
            """
        )
        dept_series = cur.fetchall()
        # Convert Decimal → float for JSON serialisation
        for row in dept_series:
            row["avg_hrs"] = float(row["avg_hrs"] or 0)
            row["total"]    = int(row["total"])
            row["resolved"] = int(row["resolved"] or 0)

        cur.execute(
            """
            SELECT
                DATE_FORMAT(created_at, '%Y-%m') AS month,
                COUNT(*)                          AS cnt
            FROM   complaints
            WHERE  created_at >= DATE_SUB(NOW(), INTERVAL 6 MONTH)
            GROUP  BY month
            ORDER  BY month ASC
            """
        )
        monthly_volume = [
            {"month": r["month"], "cnt": int(r["cnt"])}
            for r in cur.fetchall()
        ]

    return jsonify({
        "status_counts":   status_counts,
        "priority_counts": priority_counts,
        "dept_series":     dept_series,
        "monthly_volume":  monthly_volume,
    })


# ---------------------------------------------------------------------------
# User Management  (activate / deactivate student accounts)
# ---------------------------------------------------------------------------
@admin_bp.route("/users")
@admin_required
def user_list():
    """List all student accounts with their complaint count and status."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT
                u.user_id, u.full_name, u.email,
                u.roll_number, u.is_active, u.created_at,
                COUNT(c.complaint_id) AS complaint_count
            FROM   users u
            LEFT JOIN complaints c ON c.student_id = u.user_id
            WHERE  u.role = 'student'
            GROUP  BY u.user_id
            ORDER  BY u.created_at DESC
            """
        )
        users = cur.fetchall()

    return render_template("admin/user_list.html", users=users)


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id: int):
    """
    Flip is_active for a student account.
    Admins cannot deactivate their own account.
    """
    if user_id == session.get("user_id"):
        flash("You cannot deactivate your own account.", "warning")
        return redirect(url_for("admin.user_list"))

    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET    is_active = NOT is_active
                WHERE  user_id   = %s
                AND    role      = 'student'
                """,
                (user_id,)
            )
            db.commit()
        flash("User account status updated.", "success")
        logger.info("User toggled | target_user=%s by_admin=%s",
                    user_id, session.get("user_id"))
    except Exception as exc:
        db.rollback()
        logger.error("Toggle user failed | user_id=%s | %s", user_id, exc)
        flash("Operation failed. Please try again.", "danger")

    return redirect(url_for("admin.user_list"))
