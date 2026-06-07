import sqlite3
from config import Config

def init_db():
    # Connects to invoices.db using the configuration path
    conn = sqlite3.connect(Config.DATABASE_PATH)
    cursor = conn.cursor()
    
    # Create the invoices storage table structure
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_name TEXT NOT NULL,
            invoice_number TEXT NOT NULL,
            invoice_date TEXT NOT NULL,
            tax_amount REAL DEFAULT 0.00,
            total_amount REAL DEFAULT 0.00,
            file_name TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create performance optimization indexes for dashboard matrix filters
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_invoices_vendor ON invoices(vendor_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_invoices_number ON invoices(invoice_number)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(invoice_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_invoices_uploaded ON invoices(uploaded_at)')
    
    conn.commit()
    conn.close()
    print("SQLite Database initialized with invoices structure and performance indexes successfully!")

if __name__ == '__main__':
    init_db()