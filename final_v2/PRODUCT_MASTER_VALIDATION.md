# Product Master Validation - Item Entry

## Overview
This document describes the **mandatory field validation** implemented across all three Item Entry modes (PDF Upload, Phone Scan, Manual Entry) to ensure data completeness before GRN confirmation.

## Business Requirement
Before confirming any GRN (Goods Receipt Note), the system now validates that:

1. **Invoice Header** has all required fields
2. **Each line item** exists in Product Master with complete data
3. **Selected Supplier** has required contact information
4. **QC inspection** is completed for all items

This ensures no incomplete data enters the inventory system.

---

## Validation Rules

### 1. Invoice Header Fields (Mandatory)
- ✅ **Invoice Number** - Required, must not be empty
- ✅ **Invoice Billing Date** - Required, must be a valid date
- ✅ **Supplier** - Required, must be selected from dropdown
- ✅ **Total Invoice Value** - Required, must be a number

### 2. Line Item Basic Validation
- ✅ **At least one item** must be present
- ✅ **Item Name (Description)** - Required for each line
- ✅ **Quantity** - Must be > 0
- ✅ **Unit Price** - Must be ≥ 0

### 3. Product Master Validation (NEW - STRICT)
**EACH line item must:**
- ✅ **Exist in Product Master** (matched by name/SKU/barcode)
- ✅ Have **Item Code (SKU)** - Not empty
- ✅ Have **Item Name** - Not empty
- ✅ Have **Category** - Not empty
- ✅ Have **Subcategory** - Not empty
- ✅ Have **Unit of Measure** - Not empty
- ✅ Have **Reorder Limit Level** - Not null/empty
- ✅ Have **HSN/SAC Code** - Not empty
- ✅ Have **Physical Storage Area** - Not empty

**⚠️ If any item is missing from Product Master or has incomplete data, GRN confirmation is BLOCKED.**

### 4. Supplier Validation (NEW)
The selected supplier must have:
- ✅ **Company Name** - Required
- ✅ **Phone Number** - Required

### 5. QC Validation (Existing)
- ✅ **All items** must have completed QC inspection
- ✅ QC badge must show "QC Confirmed" or "QC Rejected"

---

## Workflow Impact

### Before This Change
1. User uploads invoice (PDF/Phone/Manual)
2. AI extracts items (may be new items not in system)
3. User confirms GRN ✅
4. Items added to inventory (even with incomplete data)

### After This Change (STRICT MODE)
1. User uploads invoice (PDF/Phone/Manual)
2. AI extracts items
3. System validates each item:
   - ❌ If item NOT in Product Master → **BLOCK GRN**
   - ❌ If item missing required fields → **BLOCK GRN**
4. User must:
   - Go to **Product Master** page
   - Add/Update items with complete data (SKU, Category, Unit, etc.)
   - Return to Item Entry
5. Retry GRN confirmation ✅

---

## User Experience

### Error Messages

#### Missing Items (Not in Product Master)
```
❌ Product Master Validation Failed

Cannot confirm GRN until all items are properly registered in Product Master.

Items NOT in Product Master (2):
  • Paracetamol 500mg Tablets
  • Aspirin 75mg Capsules

Required for EACH item:
  • Item Code (SKU)
  • Item Name
  • Category
  • Subcategory
  • Unit of Measure
  • Reorder Limit Level
  • HSN/SAC Code
  • Physical Storage Area

Action Required:
1. Go to "Product Master" page
2. Add/Update missing items with complete data
3. Return here and try again
```

#### Incomplete Items (Missing Fields)
```
❌ Product Master Validation Failed

Cannot confirm GRN until all items are properly registered in Product Master.

Items with INCOMPLETE data (1):
  • Paracetamol 500mg
    Missing: Category, HSN/SAC Code, Physical Storage Area

Required for EACH item:
  • Item Code (SKU)
  • Item Name
  • Category
  • Subcategory
  • Unit of Measure
  • Reorder Limit Level
  • HSN/SAC Code
  • Physical Storage Area

Action Required:
1. Go to "Product Master" page
2. Add/Update missing items with complete data
3. Return here and try again
```

#### Supplier Missing Data
```
❌ Supplier Data Incomplete

The selected supplier is missing required information:

  • Phone Number

Please update supplier "ABC Pharmaceuticals Ltd" in Supplier Master with:
  • Company Name
  • Phone Number

Then return here and try again.
```

---

## Item Matching Logic (ULTRA STRICT)

The system uses **5 strategies** to match invoice items with Product Master:

### Strategy 1: Exact Name Match
```javascript
"Paracetamol 500mg Tablets" === "Paracetamol 500mg Tablets"
```

### Strategy 2: SKU Exact Match
```javascript
Invoice item: "P500T"
Product Master SKU: "P500T" ✅
```

### Strategy 3: Barcode Exact Match
```javascript
Invoice item: "8901234567890"
Product Master Barcode: "8901234567890" ✅
```

### Strategy 4: Product Name Contains (70% Rule)
```javascript
Extracted: "Paracetamol 500mg"  (18 chars)
Product Master: "Paracetamol 500mg Tablets Box of 10"  (42 chars)
Ratio: 18/42 = 0.43 ❌ (less than 70%, rejected)

Extracted: "Paracetamol 500mg Tablets"  (25 chars)
Product Master: "Paracetamol 500mg Tablets"  (25 chars)
Ratio: 25/25 = 1.0 ✅ (100%, accepted)
```

### Strategy 5: Abbreviation Expansion (STRICT)
```javascript
Extracted: "Paracetamol 500mg Tabs"
Expanded: "Paracetamol 500mg Tablets"
Product Master: "Paracetamol 500mg Tablets" ✅

Abbreviations supported:
- tabs → tablets
- caps → capsules
- inj → injection
```

**⚠️ Fuzzy matching is DISABLED to prevent false matches.**

---

## Technical Implementation

### Files Modified
1. **`templates/item_entry.html`**
   - Added `validateAllItemsInProductMaster()` function
   - Added `validateSupplierData()` function
   - Updated `saveInvoiceToDB()` to call validation before GRN
   - Added detailed error messages with actionable instructions

2. **`app.py`**
   - Added `/api/supplier/<id>` endpoint
   - Returns supplier data for validation

### API Endpoints Used
```javascript
// Fetch all products for validation
GET /api/products
Returns: { products: [...] }

// Fetch supplier by ID
GET /api/supplier/<supplier_id>
Returns: { id, supplier_name, phone, ... }
```

### Validation Flow
```javascript
async function saveInvoiceToDB() {
  // 1. Validate invoice header
  if (!validateMandatoryFields()) return;
  
  // 2. Validate line items basic
  for (item of allItems) {
    if (!item.description) return;
    if (qty <= 0) return;
    if (price < 0) return;
  }
  
  // 3. NEW: Validate Product Master completeness
  const pmValidation = await validateAllItemsInProductMaster(allItems);
  if (!pmValidation.valid) {
    alert("Product Master Validation Failed...");
    return; // ⛔ BLOCK GRN
  }
  
  // 4. NEW: Validate Supplier data
  const supplierValidation = await validateSupplierData(supplierId);
  if (!supplierValidation.valid) {
    alert("Supplier Data Incomplete...");
    return; // ⛔ BLOCK GRN
  }
  
  // 5. Validate QC completion
  if (!allQCCompleted) {
    alert("QC Required for All Items...");
    return;
  }
  
  // 6. Proceed with GRN posting
  _doSaveInvoice();
}
```

---

## Testing Scenarios

### Test Case 1: New Item (Not in Product Master)
1. Upload invoice with "New Medicine XYZ"
2. AI extracts item
3. Click "Confirm GRN"
4. **Expected:** Error - "Items NOT in Product Master: New Medicine XYZ"
5. Go to Product Master, add "New Medicine XYZ" with all fields
6. Return, click "Confirm GRN"
7. **Expected:** Success ✅

### Test Case 2: Incomplete Item (Missing Fields)
1. Product Master has "Aspirin 75mg" but missing "HSN/SAC Code"
2. Upload invoice with "Aspirin 75mg"
3. AI extracts and matches item
4. Click "Confirm GRN"
5. **Expected:** Error - "Incomplete data: Aspirin 75mg, Missing: HSN/SAC Code"
6. Go to Product Master, update "Aspirin 75mg" with HSN/SAC
7. Return, click "Confirm GRN"
8. **Expected:** Success ✅

### Test Case 3: Supplier Missing Phone
1. Supplier "ABC Ltd" exists but phone is empty
2. Upload invoice from "ABC Ltd"
3. Select supplier from dropdown
4. Click "Confirm GRN"
5. **Expected:** Error - "Supplier Data Incomplete: Phone Number required"
6. Go to Supplier Master, update "ABC Ltd" with phone
7. Return, click "Confirm GRN"
8. **Expected:** Success ✅

### Test Case 4: All Valid (Happy Path)
1. All items exist in Product Master with complete data
2. Supplier has all required fields
3. QC completed for all items
4. Click "Confirm GRN"
5. **Expected:** Success - GRN posted, QR codes generated ✅

---

## Migration Impact

### Existing Users
- **⚠️ Breaking Change:** GRNs with incomplete Product Master data will now be BLOCKED
- **Action Required:** Users must ensure all inventory items are registered in Product Master before receiving goods

### Data Cleanup Required
Before deploying, existing Product Master records should be reviewed:
```sql
-- Find products missing required fields
SELECT 
  id, item_name,
  CASE WHEN sku IS NULL OR sku = '' THEN 'Missing SKU' END,
  CASE WHEN category IS NULL OR category = '' THEN 'Missing Category' END,
  CASE WHEN subcategory IS NULL OR subcategory = '' THEN 'Missing Subcategory' END,
  CASE WHEN unit IS NULL OR unit = '' THEN 'Missing Unit' END,
  CASE WHEN reorder_level IS NULL THEN 'Missing Reorder Level' END,
  CASE WHEN hsn_sac_code IS NULL OR hsn_sac_code = '' THEN 'Missing HSN/SAC' END,
  CASE WHEN storage_location IS NULL OR storage_location = '' THEN 'Missing Storage' END
FROM products
WHERE 
  sku IS NULL OR sku = '' OR
  category IS NULL OR category = '' OR
  subcategory IS NULL OR subcategory = '' OR
  unit IS NULL OR unit = '' OR
  reorder_level IS NULL OR
  hsn_sac_code IS NULL OR hsn_sac_code = '' OR
  storage_location IS NULL OR storage_location = '';
```

---

## Configuration

### Optional Fields (NOT Validated)
- Vendor Name (detected by AI, not mandatory)
- Inspection Properties (explicitly kept optional per user requirement)
- Supplier: Contact Person, Email, Address

### Required Fields (Validated)
**Invoice Header:**
- Invoice Number
- Invoice Billing Date
- Supplier
- Total Invoice Value

**Line Items:**
- Item Name
- Quantity > 0
- Unit Price ≥ 0

**Product Master (for EACH item):**
- Item Code (SKU)
- Item Name
- Category
- Subcategory
- Unit of Measure
- Reorder Limit Level
- HSN/SAC Code
- Physical Storage Area

**Supplier:**
- Company Name
- Phone Number

---

## Rollback Plan

If this validation causes issues, revert `item_entry.html` to remove:
1. `validateAllItemsInProductMaster()` function
2. `validateSupplierData()` function
3. Calls to these functions in `saveInvoiceToDB()`

The system will return to the previous behavior (allowing incomplete data).

---

## Support

### Common User Questions

**Q: Why can't I confirm GRN for a new item?**
A: All items must be registered in Product Master with complete data before receiving. Go to Product Master, add the item, then return.

**Q: What if I don't know the HSN/SAC code?**
A: HSN/SAC code is mandatory for tax compliance. Check with your accounts team or look up the code online before adding the item.

**Q: Can I skip this validation for urgent receipts?**
A: No. This validation ensures data integrity and compliance. However, you can quickly add items to Product Master (takes ~2 minutes per item).

**Q: Does this apply to all 3 entry modes?**
A: Yes. PDF Upload, Phone Scan, and Manual Entry all use the same validation logic.

---

## Status
✅ **IMPLEMENTED** - All three Item Entry modes now enforce Product Master validation before GRN confirmation.

Date: 2026-07-24
Version: v2.0
