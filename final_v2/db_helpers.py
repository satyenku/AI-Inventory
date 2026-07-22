# db_helpers.py
import sqlite3
from config import Config

def get_db_connection():
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn

def log_stock_movement(cursor, product_id, movement_type, reference_table, reference_id, quantity_change):
    """Adjust product stock and write to stock_ledger.

    quantity_change: Positive for Stock In (GRN, RETURN), negative for Stock Out (ISSUE).

    Notes on return/issue logic:
    - Stock decreases only when an ISSUE is logged.
    - Stock increases only when a RETURN is logged.
    - If a ledger entry already exists for the same (reference_table, reference_id, movement_type),
      we treat it as an exact duplicate and do nothing.
    """

    # Prevent duplicate ledger entries for same source (idempotency).
    # IMPORTANT: reference_id is unique per row in its source table.
    exists = None
    try:
        exists = cursor.execute(
            "SELECT 1 FROM stock_ledger WHERE reference_table = ? AND reference_id = ? AND movement_type = ? LIMIT 1",
            (reference_table, reference_id, movement_type),
        ).fetchone()
    except Exception:
        # ignore read errors and proceed to attempt the write
        exists = None

    if exists:
        return

    cursor.execute("SELECT current_stock FROM products WHERE id = ?", (product_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Unable to process. Product ID {product_id} does not exist.")

    old_balance = float(row['current_stock'] or 0)
    new_balance = old_balance + float(quantity_change or 0)

    if new_balance < 0:
        raise ValueError(
            f"Insufficient stock for product {product_id}: current_stock={old_balance}, quantity_change={quantity_change}"
        )

    # Update running stock metrics first; if ledger insert fails, rollback via exception.
    cursor.execute(
        "UPDATE products SET current_stock = ? WHERE id = ?",
        (new_balance, product_id),
    )

    try:
        cursor.execute(
            """
            INSERT INTO stock_ledger (product_id, movement_type, reference_table, reference_id, quantity_change, balance_after)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (product_id, movement_type, reference_table, reference_id, quantity_change, new_balance),
        )
    except Exception:
        # revert product stock and re-raise
        try:
            cursor.execute(
                "UPDATE products SET current_stock = ? WHERE id = ?",
                (old_balance, product_id),
            )
        except Exception:
            pass
        raise



def get_product_properties(product_id):
    """Return a list of property rows for a product."""
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "SELECT id, property_name, min_value, max_value, method FROM product_properties WHERE product_id = ? ORDER BY id ASC",
            (product_id,)
        )
        rows = cur.fetchall()
        return rows
    finally:
        conn.close()


def insert_product_property(cursor, product_id, name, min_value=None, max_value=None, method=None):
    """Insert a single inspection property using provided cursor."""
    cursor.execute(
        "INSERT INTO product_properties (product_id, property_name, min_value, max_value, method) VALUES (?, ?, ?, ?, ?)",
        (product_id, name, min_value, max_value, method)
    )