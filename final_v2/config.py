# config.py
import os

class Config:
    _default_secret = None

    @classmethod
    def _get_secret_key(cls):
        key = os.environ.get("FLASK_SECRET_KEY")
        if not key:
            import warnings
            warnings.warn(
                "WARNING: FLASK_SECRET_KEY not set. Using a generated key — sessions will reset on restart. "
                "Set FLASK_SECRET_KEY in your .env for production.",
                RuntimeWarning,
                stacklevel=3,
            )
            if cls._default_secret is None:
                import secrets as _s
                cls._default_secret = _s.token_hex(32)
            return cls._default_secret
        return key

    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or __import__('secrets').token_hex(32)
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
    DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'inventory.db')
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    # Pre-create standard storage directories
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    @staticmethod
    def validate():
        if not os.environ.get("GEMINI_API_KEY"):
            import warnings
            warnings.warn(
                "WARNING: GEMINI_API_KEY is not set. AI invoice extraction will be unavailable.",
                RuntimeWarning,
                stacklevel=2,
            )