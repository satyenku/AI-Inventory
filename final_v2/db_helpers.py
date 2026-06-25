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
    """
    Adjusts standard product balances and creates audit trails inside stock_ledger.
    quantity_change: Positive for Stock In (GRN, Returns), negative for Stock Out (Issues).
    """
    cursor.execute("SELECT current_stock FROM products WHERE id = ?", (product_id,))
    row = cursor.fetchone()
    if not row:
         raise ValueError(f"Unable to process. Product ID {product_id} does not exist.")
         
    old_balance = row['current_stock']
    new_balance = old_balance + quantity_change

    if new_balance < 0:
        raise ValueError(
            f"Insufficient stock for product {product_id}: current_stock={old_balance}, quantity_change={quantity_change}"
        )

    # Update running stock metrics
    cursor.execute(
        "UPDATE products SET current_stock = ? WHERE id = ?",
        (new_balance, product_id)
    )
    
    # Insert Audit Trail Log
    cursor.execute("""
        INSERT INTO stock_ledger (product_id, movement_type, reference_table, reference_id, quantity_change, balance_after)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (product_id, movement_type, reference_table, reference_id, quantity_change, new_balance))