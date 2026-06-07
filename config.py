import os

class Config:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    DATABASE_PATH = os.path.join(BASE_DIR, 'invoices.db')
    
    # Upload constraints
    ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
    
    # Path pointing to your windows installed Tesseract OCR application binary
    TESSERACT_CMD = r'C:\Program Files\Tesseract-OCR\tesseract.exe'