"""
Database schema for the AI-Inventory project.

Covers the full flow seen in the wireframes:
- Suppliers
- Product/Item master
- Invoices + line items
- Goods Receipt / Item Entry (GRN) + line items  -> increases stock
- Item Issues (to dept/work order) + line items  -> decreases stock
- Item Returns + line items                      -> increases stock back
- Stock ledger (single audit trail of every movement, used for "Item Inventory Status")
- Users (login)

Run:  python init_db.py
"""

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
    item_code           TEXT NOT NULL UNIQUE,        -- SKU / auto or manual
    barcode             TEXT UNIQUE,
    item_name           TEXT NOT NULL,
    category            TEXT,
    subcategory         TEXT,
    unit                TEXT NOT NULL DEFAULT 'Nos',  -- Nos / Kg / etc.
    hsn_sac_code        TEXT,
    description         TEXT,
    storage_location    TEXT,                         -- e.g. "Main Store / Rack A"
    min_stock_level     REAL DEFAULT 0,
    max_stock_level     REAL DEFAULT 0,
    reorder_level       REAL DEFAULT 0,
    current_stock       REAL NOT NULL DEFAULT 0,       -- denormalized running balance
    status              TEXT NOT NULL DEFAULT 'Active', -- Active / Inactive
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- INVOICES  (header extracted by the Donut model)
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
    raw_extraction  TEXT,                  -- raw JSON output from Donut model
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
    condition       TEXT     -- Good / Damaged / Expired
);

-- ============================================================
-- STOCK LEDGER - single audit trail for every stock movement
-- Used to compute / verify "Item Inventory Status" reports
-- ============================================================
CREATE TABLE IF NOT EXISTS stock_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    movement_type   TEXT NOT NULL,        -- GRN / ISSUE / RETURN / ADJUSTMENT
    reference_table TEXT,                 -- 'grn', 'item_issues', 'inventory_returns'
    reference_id    INTEGER,
    quantity_change REAL NOT NULL,        -- positive = in, negative = out
    balance_after   REAL NOT NULL,
    moved_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_invoices_vendor   ON invoices(vendor_name);
CREATE INDEX IF NOT EXISTS idx_invoices_number   ON invoices(invoice_number);
CREATE INDEX IF NOT EXISTS idx_invoices_date     ON invoices(invoice_date);
CREATE INDEX IF NOT EXISTS idx_invoices_uploaded ON invoices(uploaded_at);

CREATE INDEX IF NOT EXISTS idx_products_code     ON products(item_code);
CREATE INDEX IF NOT EXISTS idx_products_barcode  ON products(barcode);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);

CREATE INDEX IF NOT EXISTS idx_grn_no            ON grn(grn_no);
CREATE INDEX IF NOT EXISTS idx_issue_no          ON item_issues(issue_slip_no);
CREATE INDEX IF NOT EXISTS idx_return_id         ON inventory_returns(return_id);

CREATE INDEX IF NOT EXISTS idx_ledger_product    ON stock_ledger(product_id);
CREATE INDEX IF NOT EXISTS idx_ledger_moved_at   ON stock_ledger(moved_at);
"""


def init_db():
    conn = sqlite3.connect(Config.DATABASE_PATH)
    cursor = conn.cursor()
    cursor.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"Database initialized successfully at {Config.DATABASE_PATH}")


if __name__ == '__main__':
    init_db()