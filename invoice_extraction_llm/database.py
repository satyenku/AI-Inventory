import sqlite3
from config import Config

def init_db():
    """Creates the database table if it doesn't exist yet."""
    with sqlite3.connect(Config.DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT,
                invoice_date TEXT,
                vendor_name TEXT,
                tax_details TEXT,
                total_amount REAL,
                file_path TEXT,
                extracted_at TEXT
            )
        ''')
        conn.commit()

def save_invoice(data, file_path, timestamp):
    """Saves the clean AI data straight into SQLite."""
    with sqlite3.connect(Config.DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO invoices (invoice_number, invoice_date, vendor_name, tax_details, total_amount, file_path, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.invoice_number,
            data.invoice_date,
            data.vendor_name,
            data.tax_details,
            data.total_amount,
            file_path,
            timestamp
        ))
        conn.commit()

def get_filtered_invoices(filters):
    """Searches the database based on what the user typed."""
    query = "SELECT * FROM invoices WHERE 1=1"
    params = []
    
    if filters.get('invoice_number'):
        query += " AND invoice_number LIKE ?"
        params.append(f"%{filters.get('invoice_number')}%")
    if filters.get('vendor_name'):
        query += " AND vendor_name LIKE ?"
        params.append(f"%{filters.get('vendor_name')}%")
        
    with sqlite3.connect(Config.DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()