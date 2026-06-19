import os

class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "super_secret_session_key")
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
    DB_NAME = "invoices.db"
    
    # Automatically creates the uploads folder if it doesn't exist
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    @staticmethod
    def validate():
        if not os.environ.get("GEMINI_API_KEY"):
            raise ValueError("CRITICAL ERROR: GEMINI_API_KEY environment variable is missing!")