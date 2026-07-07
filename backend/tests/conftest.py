import os

os.environ.setdefault("APP_NAME", "Telegram Ledger API")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_DEBUG", "false")
os.environ.setdefault("APP_LOG_LEVEL", "CRITICAL")
os.environ.setdefault("APP_API_PREFIX", "/api/v1")
os.environ.setdefault(
    "APP_CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
)
os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_PORT", "5432")
os.environ.setdefault("DATABASE_NAME", "ledger_db")
os.environ.setdefault("DATABASE_USER", "ledger_user")
os.environ.setdefault("DATABASE_PASSWORD", "test-only-password")
os.environ.setdefault("DATABASE_ECHO", "false")
os.environ.setdefault("DATABASE_POOL_SIZE", "5")
os.environ.setdefault("DATABASE_MAX_OVERFLOW", "5")
os.environ.setdefault("AUTH_SECRET_KEY", "test-only-secret-key-at-least-32-characters")
os.environ.setdefault("AUTH_COOKIE_NAME", "telegram_ledger_session")
os.environ.setdefault("AUTH_COOKIE_SECURE", "false")
os.environ.setdefault("AUTH_SESSION_MINUTES", "480")
