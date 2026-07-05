import sqlite3
import json
from config import Config

DB = Config.DATABASE_PATH

def rows_to_dicts(cur, rows):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in rows]

conn = sqlite3.connect(DB)
conn.row_factory = None
cur = conn.cursor()

# Posted GRNs
cur.execute("SELECT * FROM grn WHERE status = 'Posted' OR posted_date IS NOT NULL ORDER BY id DESC")
posted_grns = rows_to_dicts(cur, cur.fetchall())

# Posted grn_items
grn_ids = [g['id'] for g in posted_grns]
if grn_ids:
    q = "SELECT * FROM grn_items WHERE grn_id IN ({})".format(','.join('?' for _ in grn_ids))
    cur.execute(q, grn_ids)
    posted_items = rows_to_dicts(cur, cur.fetchall())
else:
    posted_items = []

# Stock ledger entries for GRN
cur.execute("SELECT * FROM stock_ledger WHERE movement_type = 'GRN' ORDER BY moved_at DESC LIMIT 200")
ledger = rows_to_dicts(cur, cur.fetchall())

# Current stocks for involved products
product_ids = sorted({row.get('product_id') for row in posted_items if row.get('product_id') is not None})
if product_ids:
    q = "SELECT id, item_name, current_stock FROM products WHERE id IN ({})".format(','.join('?' for _ in product_ids))
    cur.execute(q, product_ids)
    products = rows_to_dicts(cur, cur.fetchall())
else:
    products = []

output = {
    'posted_grns': posted_grns,
    'posted_items': posted_items,
    'stock_ledger_grn': ledger,
    'products': products,
}

print(json.dumps(output, indent=2, ensure_ascii=False))
conn.close()
