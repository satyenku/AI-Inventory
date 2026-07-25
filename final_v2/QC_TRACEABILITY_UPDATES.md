# QC Data Model Update: Production Traceability via GRN Item Linking

## Summary
Updated the QC inspection data model to link every inspection record to `grn_item_id` instead of only `product_id`. This enables full production traceability from QC inspection → GRN Item → GRN → Invoice → Supplier.

## Changes Made

### 1. Database Schema Updates (`init_db.py`)

**Added `grn_item_id` foreign key to `inspection_entries` table:**

```sql
CREATE TABLE IF NOT EXISTS inspection_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    grn_item_id INTEGER,  -- NEW: Links to specific GRN item
    inspection_date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(product_id) REFERENCES products(id),
    FOREIGN KEY(grn_item_id) REFERENCES grn_items(id)  -- NEW
);
```

**Key Points:**
- `grn_item_id` is nullable for backward compatibility with existing records
- Maintains `product_id` for basic product reference
- Creates foreign key relationship to `grn_items` table

### 2. Migration Logic (`app.py` - `ensure_ledger_integrity()`)

**Added automatic migration for existing databases:**

```python
# Migration: add grn_item_id column to inspection_entries for production traceability
inspection_cols = [row[1] for row in cur.execute("PRAGMA table_info(inspection_entries)").fetchall()]
if 'grn_item_id' not in inspection_cols:
    cur.execute("ALTER TABLE inspection_entries ADD COLUMN grn_item_id INTEGER REFERENCES grn_items(id)")
    logger.info("[Migration] Added grn_item_id column to inspection_entries for GRN traceability")
```

**Key Points:**
- Runs automatically on application startup
- Safe for existing databases (non-destructive)
- Logs migration for audit trail
- Existing inspection records will have `grn_item_id` as NULL (backward compatible)

### 3. QC Save Logic Updates

#### `/api/save-qc` Endpoint Enhancements:

**Added grn_item_id capture and auto-resolution:**

```python
# NEW: Accept grn_item_id from request
grn_item_id = data.get('grn_item_id')

# NEW: If not provided, auto-resolve from invoice_number + product_id
if not grn_item_id and invoice_number and product_id:
    try:
        inv = cursor.execute("SELECT id FROM invoices WHERE invoice_number = ?", (invoice_number,)).fetchone()
        if inv:
            grn_row = cursor.execute("SELECT id FROM grn WHERE invoice_id = ? ORDER BY id DESC LIMIT 1", (inv['id'],)).fetchone()
            if grn_row:
                gi_row = cursor.execute("SELECT id FROM grn_items WHERE grn_id = ? AND product_id = ? ORDER BY id DESC LIMIT 1", (grn_row['id'], product_id)).fetchone()
                if gi_row:
                    grn_item_id = gi_row['id']
    except Exception as e:
        logger.warning("[api_save_qc] Could not auto-resolve grn_item_id: %s", e)

# NEW: Insert with grn_item_id
cursor.execute("""
    INSERT INTO inspection_entries (product_id, grn_item_id, inspection_date)
    VALUES (?, ?, ?)
""", (product_id, grn_item_id, inspection_date))
```

**Key Features:**
- Accepts `grn_item_id` directly from frontend if provided
- Auto-resolves `grn_item_id` when invoice_number is known
- Gracefully handles missing grn_item_id (remains NULL)
- Updates grn_items.qc_status using grn_item_id when available (more precise)

#### `/api/save-inspection` Endpoint Enhancements:

Similar changes to support grn_item_id capture and auto-resolution.

### 4. QC Load Logic Updates

#### `/qc-sheet` Route Enhancements:

```python
grn_item_id = request.args.get('grn_item_id', type=int)  # NEW: accept parameter

# NEW: Auto-resolve if not provided
if not grn_item_id and invoice_number and product:
    try:
        inv = conn.execute("SELECT id FROM invoices WHERE invoice_number = ?", (invoice_number,)).fetchone()
        if inv:
            grn_row = conn.execute("SELECT id FROM grn WHERE invoice_id = ? ORDER BY id DESC LIMIT 1", (inv['id'],)).fetchone()
            if grn_row:
                gi_row = conn.execute("SELECT id FROM grn_items WHERE grn_id = ? AND product_id = ? ORDER BY id DESC LIMIT 1", (grn_row['id'], product["id"])).fetchone()
                if gi_row:
                    grn_item_id = gi_row['id']
    except Exception:
        pass
```

### 5. New Traceability APIs

#### GET `/api/qc-traceability/<inspection_id>`
Retrieves full traceability chain for a QC inspection:

**Returns:**
```json
{
  "inspection": {
    "inspection_id": 123,
    "inspection_date": "2026-07-24",
    "product_id": 45,
    "grn_item_id": 67,
    "item_code": "ITEM-001",
    "item_name": "Product Name",
    "grn_quantity": 100,
    "grn_no": "GRN-INV123-ABC",
    "invoice_number": "INV123",
    "invoice_date": "2026-07-20",
    "supplier_name": "ABC Suppliers",
    "gst_number": "22AAAAA0000A1Z5",
    "contact_person": "John Doe",
    "phone": "+91-1234567890"
  },
  "details": [...],
  "traceability": {
    "has_grn_link": true,
    "grn_no": "GRN-INV123-ABC",
    "invoice_number": "INV123",
    "supplier_name": "ABC Suppliers",
    "supplier_gst": "22AAAAA0000A1Z5",
    "received_date": "2026-07-20"
  }
}
```

**Traceability Chain:**
```
Inspection → GRN Item → GRN → Invoice → Supplier → Product
```

#### GET `/api/qc-inspections`
Lists all QC inspections with basic traceability info.

**Query Parameters (all optional):**
- `product_id` - Filter by product
- `grn_id` - Filter by GRN
- `supplier_id` - Filter by supplier
- `from_date` - Filter by inspection date (start)
- `to_date` - Filter by inspection date (end)

**Returns:**
```json
{
  "inspections": [
    {
      "inspection_id": 123,
      "inspection_date": "2026-07-24",
      "item_code": "ITEM-001",
      "item_name": "Product Name",
      "qc_status": "Confirmed",
      "grn_no": "GRN-INV123-ABC",
      "invoice_number": "INV123",
      "supplier_name": "ABC Suppliers"
    }
  ]
}
```

## Traceability Benefits

### Before (Product-only linking):
- ❌ Cannot distinguish between different GRN batches of same product
- ❌ Cannot trace QC back to specific invoice
- ❌ Cannot identify supplier from QC record
- ❌ No link to purchase price, batch number, or expiry date

### After (GRN Item linking):
- ✅ Every QC inspection linked to specific GRN item
- ✅ Full traceability: QC → GRN → Invoice → Supplier
- ✅ Access to batch number, expiry date, unit price from GRN
- ✅ Can filter QC inspections by supplier or invoice
- ✅ Audit trail shows exact received quantity and date
- ✅ Supports lot-based quality control

## Backward Compatibility

### Existing Data:
- Old inspection records will have `grn_item_id = NULL`
- System continues to work with product_id alone
- No data loss or corruption

### Migration Path:
- New inspections automatically capture grn_item_id when available
- Auto-resolution logic fills grn_item_id when invoice_number is known
- Manual updates can be done via SQL if needed:
  ```sql
  UPDATE inspection_entries
  SET grn_item_id = (
    SELECT gi.id 
    FROM grn_items gi
    JOIN grn g ON gi.grn_id = g.id
    JOIN invoices inv ON g.invoice_id = inv.id
    WHERE gi.product_id = inspection_entries.product_id
      AND inv.invoice_number = '<known_invoice>'
    ORDER BY gi.id DESC
    LIMIT 1
  )
  WHERE grn_item_id IS NULL
    AND inspection_date >= '2026-01-01';
  ```

## Usage Examples

### Frontend: Save QC with Traceability
```javascript
fetch('/api/save-qc', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    product_id: 45,
    grn_item_id: 67,  // NEW: Include for full traceability
    invoice_number: 'INV123',
    inspection_date: '2026-07-24',
    details: [...]
  })
});
```

### Frontend: Load QC with Traceability
```javascript
fetch('/qc-sheet?product_id=45&invoice_number=INV123&grn_item_id=67')
  .then(response => response.text());
```

### Query: Find all QC inspections for a supplier
```javascript
fetch('/api/qc-inspections?supplier_id=5')
  .then(response => response.json());
```

### Query: Full traceability for an inspection
```javascript
fetch('/api/qc-traceability/123')
  .then(response => response.json())
  .then(data => {
    console.log(`Inspection traced to: 
      GRN: ${data.traceability.grn_no}
      Invoice: ${data.traceability.invoice_number}
      Supplier: ${data.traceability.supplier_name}`);
  });
```

## UI Considerations (No Changes Required)

The existing UI continues to work without modification because:
1. `grn_item_id` is optional (nullable)
2. Auto-resolution logic fills it from invoice_number when available
3. product_id remains the primary link
4. Frontend can optionally enhance to show/capture grn_item_id

## Testing Checklist

- [x] Fresh database initialization with new schema
- [x] Existing database migration (add column)
- [x] Save QC with grn_item_id provided
- [x] Save QC with auto-resolution (invoice_number only)
- [x] Save QC without grn_item_id (backward compatible)
- [x] Load QC sheet with grn_item_id
- [x] Traceability API returns complete chain
- [x] List inspections with filters
- [x] Old inspection records still accessible

## Rollback Procedure (if needed)

If issues arise, the column can be removed safely:
```sql
-- Create backup
CREATE TABLE inspection_entries_backup AS SELECT * FROM inspection_entries;

-- Remove column (SQLite doesn't support DROP COLUMN directly)
-- Requires table recreation:
CREATE TABLE inspection_entries_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    inspection_date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(product_id) REFERENCES products(id)
);

INSERT INTO inspection_entries_new (id, product_id, inspection_date, created_at)
SELECT id, product_id, inspection_date, created_at FROM inspection_entries;

DROP TABLE inspection_entries;
ALTER TABLE inspection_entries_new RENAME TO inspection_entries;
```

## Future Enhancements

1. **Batch QC Reports**: Generate reports by GRN batch
2. **Supplier QC Trends**: Track quality by supplier over time
3. **Invoice-level QC Summary**: Show QC status for all items in an invoice
4. **Expiry Tracking**: Link QC to expiry dates from GRN items
5. **Cost Analysis**: Correlate QC results with purchase prices

## Files Modified

1. `init_db.py` - Schema update
2. `app.py` - Migration, save/load logic, new APIs
3. `QC_TRACEABILITY_UPDATES.md` - This documentation

---

**Migration Status**: ✅ Complete and Backward Compatible
**Production Ready**: ✅ Yes (tested with existing data)
**Breaking Changes**: ❌ None
