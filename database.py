import sqlite3
from config import Config

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    conn = sqlite3.connect(Config.DATABASE_PATH)
    # This allows us to access columns by name (like row['vendor_name']) instead of index tuples
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Creates the invoices table if it doesn't exist yet."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create the invoices table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT,
            vendor_name TEXT,
            invoice_date TEXT,
            total_amount REAL,
            tax_amount REAL,
            file_path TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    print("Database initialized successfully!")

if __name__ == '__main__':
    # Running this file directly will create the database
    init_db()