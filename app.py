"""
app.py
------
Flask application factory for the Smart College Complaint
Management System.

Structure:
    create_app()        → builds and returns the configured Flask app
    get_db()            → returns a request-scoped MySQL connection
    close_db()          → tears down the connection after each request
    generate_ticket_id()→ utility for unique CMP-YYYYMMDD-XXXX IDs
    allowed_file()      → validates upload extensions

Blueprints registered here:
    auth_bp       (routes/auth.py)       → /auth/*        login, register, logout
    complaints_bp (routes/complaints.py) → /complaints/*  submit, track, history
    admin_bp      (routes/admin.py)      → /admin/*       dashboard, manage, analytics

Run locally:
    $env:APP_ENV = "development"          # PowerShell
    $env:MYSQL_PASSWORD = "your_password"
    python app.py
"""

import os
import uuid
import logging
from datetime import datetime, timezone

import pymysql
import pymysql.cursors
from flask import Flask, g, current_app

from config import get_config


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application Factory
# ---------------------------------------------------------------------------
def create_app(config_override: dict | None = None) -> Flask:
    """
    Create and configure the Flask application.

    Parameters
    ----------
    config_override : dict, optional
        Key-value pairs that overwrite config values at runtime.
        Useful for tests (e.g. pass {"TESTING": True, "MYSQL_DB": "cms_test"}).

    Returns
    -------
    Flask
        A fully configured Flask application instance.
    """
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # ── Load configuration ────────────────────────────────────────────────
    app.config.from_object(get_config())
    if config_override:
        app.config.update(config_override)

    # ── Ensure the upload directory exists ───────────────────────────────
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # ── Tear down DB connection at end of every request ──────────────────
    app.teardown_appcontext(close_db)

    # ── Register Blueprints ──────────────────────────────────────────────
    _register_blueprints(app)

    # ── Pre-load NLP model (avoid cold-start latency on first request) ──
    with app.app_context():
        try:
            from nlp_engine import engine as nlp_engine
            if not nlp_engine.is_loaded:
                nlp_engine.load()
                logger.info("NLP model pre-loaded at startup")
        except FileNotFoundError:
            logger.warning(
                "NLP model file not found at startup — "
                "run `python nlp_trainer.py` to generate it."
            )
        except Exception as exc:
            logger.error("NLP model load failed at startup: %s", exc)

    logger.info("App created | env=%s | db=%s",
                os.environ.get("APP_ENV", "development"),
                app.config["MYSQL_DB"])

    return app


def _register_blueprints(app: Flask) -> None:
    """Import and register all route blueprints."""
    from routes.auth import auth_bp
    app.register_blueprint(auth_bp, url_prefix="/auth")

    from routes.complaints import complaints_bp
    app.register_blueprint(complaints_bp, url_prefix="/complaints")

    from routes.admin import admin_bp
    app.register_blueprint(admin_bp, url_prefix="/admin")


# ---------------------------------------------------------------------------
# Database helpers  (request-scoped, stored on Flask's g object)
# ---------------------------------------------------------------------------
def get_db() -> pymysql.connections.Connection:
    """
    Return the MySQL connection for the current request context.
    Opens a new connection if one does not yet exist for this request.

    All queries go through this helper so every connection uses the
    same settings (charset, autocommit=False for explicit transaction
    control, DictCursor for named-column access).

    Usage inside a route or helper:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT ...", (param,))
    """
    if "db" not in g:
        cfg = current_app.config
        g.db = pymysql.connect(
            host=cfg["MYSQL_HOST"],
            port=cfg["MYSQL_PORT"],
            user=cfg["MYSQL_USER"],
            password=cfg["MYSQL_PASSWORD"],
            database=cfg["MYSQL_DB"],
            charset=cfg["MYSQL_CHARSET"],
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,       # explicit commit/rollback
            connect_timeout=10,
        )
        logger.debug("DB connection opened for request")
    return g.db


def close_db(exception: BaseException | None = None) -> None:
    """
    Close and discard the DB connection at the end of the request.
    If an unhandled exception occurred the connection is rolled back
    first to avoid leaving partial transactions open.
    """
    db = g.pop("db", None)
    if db is not None:
        if exception:
            db.rollback()
            logger.warning("DB rollback due to exception: %s", exception)
        db.close()
        logger.debug("DB connection closed")


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------
def generate_ticket_id() -> str:
    """
    Generate a human-readable, unique complaint ticket ID.

    Format: CMP-YYYYMMDD-<6-char uppercase hex>
    Example: CMP-20261003-3F8A12

    The date component makes IDs self-describing in admin views;
    the random hex suffix makes collisions statistically impossible.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    random_hex = uuid.uuid4().hex[:6].upper()
    return f"CMP-{date_str}-{random_hex}"


def allowed_file(filename: str) -> bool:
    """
    Return True if the filename has a permitted extension.
    Validates against ALLOWED_EXTENSIONS in config.
    """
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in current_app.config["ALLOWED_EXTENSIONS"]


# ---------------------------------------------------------------------------
# Development entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    flask_app = create_app()
    flask_app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=flask_app.config.get("DEBUG", False),
    )
