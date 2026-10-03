"""
config.py
---------
Environment-based configuration for the Smart College Complaint
Management System.

Usage:
    Set the environment variable APP_ENV to one of:
        development  (default)
        testing
        production

    Sensitive values are read from environment variables so that
    secrets are NEVER hard-coded in source control.
    Copy .env.example → .env and fill in your local values,
    then load it with `python-dotenv` before starting the app.
"""

import os
from dotenv import load_dotenv

# Load .env into os.environ before any config values are read
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"), override=False)


class BaseConfig:
    """Shared defaults across all environments."""

    # ------------------------------------------------------------------ #
    # Session security (FR-01 / slide 27)                                 #
    # ------------------------------------------------------------------ #
    # SECRET_KEY is used by Flask to sign session cookies.
    # Must be a long, random string in production.
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "change-me-before-deploying")

    SESSION_COOKIE_HTTPONLY: bool = True   # JS cannot read the cookie
    SESSION_COOKIE_SAMESITE: str  = "Lax"  # CSRF mitigation
    PERMANENT_SESSION_LIFETIME: int = 3600  # seconds → 1 hour

    # ------------------------------------------------------------------ #
    # MySQL connection (parameterized; never string-interpolated)         #
    # ------------------------------------------------------------------ #
    MYSQL_HOST:     str = os.environ.get("MYSQL_HOST",     "localhost")
    MYSQL_PORT:     int = int(os.environ.get("MYSQL_PORT", "3306"))
    MYSQL_USER:     str = os.environ.get("MYSQL_USER",     "root")
    MYSQL_PASSWORD: str = os.environ.get("MYSQL_PASSWORD", "")
    MYSQL_DB:       str = os.environ.get("MYSQL_DB",       "college_cms")

    # Use utf8mb4 to match the schema collation
    MYSQL_CHARSET: str = "utf8mb4"

    # ------------------------------------------------------------------ #
    # File uploads                                                        #
    # ------------------------------------------------------------------ #
    UPLOAD_FOLDER:       str  = os.path.join(os.path.dirname(__file__), "uploads")
    MAX_CONTENT_LENGTH:  int  = 5 * 1024 * 1024   # 5 MB hard cap
    ALLOWED_EXTENSIONS: set  = {"pdf", "png", "jpg", "jpeg", "gif", "docx"}


class DevelopmentConfig(BaseConfig):
    DEBUG: bool    = True
    TESTING: bool  = False
    # In dev, session cookies work over plain HTTP
    SESSION_COOKIE_SECURE: bool = False


class TestingConfig(BaseConfig):
    DEBUG: bool   = False
    TESTING: bool = True
    SESSION_COOKIE_SECURE: bool = False
    # Use a separate test database
    MYSQL_DB: str = os.environ.get("MYSQL_TEST_DB", "college_cms_test")


class ProductionConfig(BaseConfig):
    DEBUG: bool    = False
    TESTING: bool  = False
    # Only send session cookies over HTTPS in production
    SESSION_COOKIE_SECURE: bool = True


# Map the APP_ENV string to a config class
_CONFIG_MAP: dict = {
    "development": DevelopmentConfig,
    "testing":     TestingConfig,
    "production":  ProductionConfig,
}


def get_config() -> type:
    """Return the appropriate config class based on APP_ENV."""
    env = os.environ.get("APP_ENV", "development").lower()
    return _CONFIG_MAP.get(env, DevelopmentConfig)
