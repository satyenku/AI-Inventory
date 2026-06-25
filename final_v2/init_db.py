# init_db.py
import sqlite3
from config import Config

SCHEMA = """
PRAGMA foreign_keys = ON;

-- ============================================================
-- USERS
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    full_name       TEXT,
    role            TEXT NOT NULL DEFAULT 'staff',   -- admin / staff / viewer
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- SUPPLIERS
-- ============================================================
CREATE TABLE IF NOT EXISTS suppliers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_name   TEXT NOT NULL,
    contact_person  TEXT,
    phone           TEXT,
    email           TEXT,
    address         TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- PRODUCT / ITEM MASTER
-- ============================================================
CREATE TABLE IF NOT EXISTS products (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    item_code           TEXT NOT NULL UNIQUE,        
    barcode             TEXT UNIQUE,
    item_name           TEXT NOT NULL,
    category            TEXT,
    subcategory         TEXT,
    unit                TEXT NOT NULL DEFAULT 'Nos',  
    hsn_sac_code        TEXT,
    description         TEXT,
    storage_location    TEXT,                         
    min_stock_level     REAL DEFAULT 0,
    max_stock_level     REAL DEFAULT 0,
    reorder_level       REAL DEFAULT 0,
    current_stock       REAL NOT NULL DEFAULT 0,       
    status              TEXT NOT NULL DEFAULT 'Active', 
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- INVOICES (header extracted by Gemini)
-- ============================================================
CREATE TABLE IF NOT EXISTS invoices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_name     TEXT NOT NULL,
    invoice_number  TEXT NOT NULL,
    invoice_date    TEXT NOT NULL,
    supplier_id     INTEGER REFERENCES suppliers(id),
    subtotal_amount REAL DEFAULT 0.00,
    tax_amount      REAL DEFAULT 0.00,
    total_amount    REAL DEFAULT 0.00,
    file_name       TEXT,
    raw_extraction  TEXT,                  
    uploaded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Line items belonging to an invoice
CREATE TABLE IF NOT EXISTS invoice_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id      INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    product_id      INTEGER REFERENCES products(id),
    item_name       TEXT NOT NULL,
    quantity        REAL NOT NULL DEFAULT 0,
    unit            TEXT,
    unit_price      REAL NOT NULL DEFAULT 0,
    tax_percent     REAL DEFAULT 0,
    line_total      REAL NOT NULL DEFAULT 0
);

-- ============================================================
-- GOODS RECEIPT / ITEM ENTRY (GRN) - stock IN
-- ============================================================
CREATE TABLE IF NOT EXISTS grn (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    grn_no          TEXT NOT NULL UNIQUE,
    invoice_id      INTEGER REFERENCES invoices(id),
    supplier_id     INTEGER REFERENCES suppliers(id),
    received_date   TEXT NOT NULL,
    received_by     INTEGER REFERENCES users(id),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS grn_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    grn_id          INTEGER NOT NULL REFERENCES grn(id) ON DELETE CASCADE,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    batch_no        TEXT,
    expiry_date     TEXT,
    quantity        REAL NOT NULL DEFAULT 0,
    unit            TEXT,
    unit_price      REAL DEFAULT 0,
    tax_percent     REAL DEFAULT 0
);

-- ============================================================
-- ITEM ISSUES - stock OUT
-- ============================================================
CREATE TABLE IF NOT EXISTS item_issues (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_slip_no   TEXT NOT NULL UNIQUE,
    issue_date      TEXT NOT NULL,
    issued_to       TEXT,
    work_order_no   TEXT,
    issued_by       INTEGER REFERENCES users(id),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS item_issue_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id        INTEGER NOT NULL REFERENCES item_issues(id) ON DELETE CASCADE,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    quantity        REAL NOT NULL DEFAULT 0,
    unit            TEXT
);

-- ============================================================
-- INVENTORY RETURNS - stock IN (reversal)
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory_returns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    return_id       TEXT NOT NULL UNIQUE,
    return_date     TEXT NOT NULL,
    returned_by     TEXT,
    department      TEXT,
    reason          TEXT,
    approved_by     INTEGER REFERENCES users(id),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inventory_return_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    return_id       INTEGER NOT NULL REFERENCES inventory_returns(id) ON DELETE CASCADE,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    quantity        REAL NOT NULL DEFAULT 0,
    unit            TEXT,
    condition       TEXT     
);

-- ============================================================
-- STOCK LEDGER - audit trail
-- ============================================================
CREATE TABLE IF NOT EXISTS stock_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    movement_type   TEXT NOT NULL,        -- GRN / ISSUE / RETURN / ADJUSTMENT
    reference_table TEXT,                 
    reference_id    INTEGER,
    quantity_change REAL NOT NULL,        
    balance_after   REAL NOT NULL,
    moved_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- BARCODE REGISTRY
-- ============================================================
CREATE TABLE IF NOT EXISTS barcode_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode_no      TEXT NOT NULL UNIQUE,
    invoice_id      INTEGER NOT NULL REFERENCES invoices(id),
    invoice_item_id INTEGER NOT NULL REFERENCES invoice_items(id),
    barcode_image   TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_invoices_vendor   ON invoices(vendor_name);
CREATE INDEX IF NOT EXISTS idx_invoices_number   ON invoices(invoice_number);
CREATE INDEX IF NOT EXISTS idx_products_code     ON products(item_code);
CREATE INDEX IF NOT EXISTS idx_products_barcode  ON products(barcode);
CREATE INDEX IF NOT EXISTS idx_ledger_product    ON stock_ledger(product_id);

-- ============================================================
-- PRODUCT INSPECTION PROPERTIES
-- ============================================================
CREATE TABLE IF NOT EXISTS product_properties (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    property_name   TEXT NOT NULL,
    min_value       REAL,
    max_value       REAL,
    method          TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_properties_product ON product_properties(product_id);
"""

def init_db():
    conn = sqlite3.connect(Config.DATABASE_PATH)
    cursor = conn.cursor()
    cursor.executescript(SCHEMA)
    
    # Create a default test user (admin / admin123)
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name, role) VALUES ('admin', 'admin123', 'Administrator', 'admin')"
        )
        
    conn.commit()
    conn.close()
    print(f"Database successfully generated at: {Config.DATABASE_PATH}")

if __name__ == '__main__':
    init_db()