# Product Recipe / Bill of Materials (BOM) Feature

## Overview
This feature allows users to define recipes for finished products, specifying which raw materials are required to manufacture one unit. When issuing inventory, users can select a finished product and quantity, and the system automatically calculates and populates the required raw materials.

## What Was Added

### 1. Database Tables
Two new tables were created to store BOM data:

- **bom_recipes**: Stores recipe headers (finished product, recipe name, description)
- **bom_recipe_items**: Stores recipe materials (raw material, quantity required, unit)

### 2. New Page: Product Recipe / BOM
**URL:** `/product-recipe`

**Features:**
- Create new recipes for finished products
- Define multiple raw materials with quantities for ONE finished product unit
- Edit existing recipes
- Delete recipes
- View all existing recipes with materials

**Example:**
```
Finished Product: Steel Chair
Recipe Materials (for ONE chair):
- Steel Pipe: 5 Nos
- Bolt: 12 Nos
- Nut: 12 Nos
- Paint: 0.5 Liter
```

### 3. Item Issue Enhancement
**Location:** `/item-issue`

**New Section:** "Issue by Recipe"

**Workflow:**
1. User selects a Finished Product (from dropdown)
2. User enters Quantity to Manufacture (e.g., 10)
3. User clicks "Apply Recipe"
4. System calculates materials: Steel Pipe (50), Bolt (120), Nut (120), Paint (5 L)
5. Materials automatically populate in the item issue table
6. User reviews and clicks "Execute Dispatch"

### 4. Backend Routes Added

- `GET/POST /product-recipe` - Main recipe management page
- `GET /product-recipe/get/<recipe_id>` - Get recipe details as JSON
- `POST /product-recipe/delete/<recipe_id>` - Delete a recipe
- `POST /api/recipe/calculate` - Calculate materials based on recipe and quantity

### 5. Navigation Updates
Added "📋 Product Recipe / BOM" link to sidebar in all pages.

## Files Modified

### New Files:
1. `migrate_add_bom.py` - Database migration script
2. `templates/product_recipe.html` - Recipe management page
3. `BOM_FEATURE_GUIDE.md` - This guide

### Modified Files:
1. `init_db.py` - Added BOM table schemas
2. `app.py` - Added 4 new routes for BOM functionality
3. `templates/item_issue.html` - Added "Issue by Recipe" section
4. `templates/item_entry.html` - Updated sidebar
5. `templates/inventory_status.html` - Updated sidebar

## How to Use

### Creating a Recipe:

1. Go to **Product Recipe / BOM** page
2. Select **Finished Product** (e.g., Steel Chair)
3. Add **Raw Materials**:
   - Select raw material
   - Enter quantity required for ONE unit
   - Select unit (Nos, Kg, Liter, etc.)
   - Click "+ Add" to add more materials
4. Click **Save Recipe**

### Editing a Recipe:

1. Go to **Product Recipe / BOM** page
2. Find the recipe in the list
3. Click **✏️ Edit** button
4. Make changes
5. Click **Update Recipe**

### Deleting a Recipe:

1. Go to **Product Recipe / BOM** page
2. Find the recipe in the list
3. Click **🗑️ Delete** button
4. Confirm deletion

### Using Recipe in Item Issue:

1. Go to **Item Issue** page
2. Fill in issue details (slip number, date, department, work order)
3. In **"Issue by Recipe"** section:
   - Select finished product
   - Enter quantity to manufacture
   - Click **✓ Apply Recipe**
4. System auto-populates materials in the dispatch table
5. Review quantities
6. Click **Execute Dispatch**

## Technical Details

### Database Schema:

```sql
CREATE TABLE bom_recipes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    finished_product_id     INTEGER NOT NULL REFERENCES products(id),
    recipe_name             TEXT,
    description             TEXT,
    created_by              INTEGER REFERENCES users(id),
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE bom_recipe_items (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id               INTEGER NOT NULL REFERENCES bom_recipes(id),
    raw_material_id         INTEGER NOT NULL REFERENCES products(id),
    quantity_required       REAL NOT NULL DEFAULT 0,
    unit                    TEXT NOT NULL,
    notes                   TEXT
);
```

### API Endpoint Example:

**Calculate Materials:**
```javascript
POST /api/recipe/calculate
{
  "finished_product_id": 5,
  "quantity": 10
}

Response:
{
  "success": true,
  "materials": [
    {
      "product_id": 12,
      "item_code": "RM-001",
      "item_name": "Steel Pipe",
      "quantity": 50,
      "unit": "Nos",
      "current_stock": 200
    },
    ...
  ]
}
```

## Important Notes

### What Was NOT Changed:
- ✅ Item Entry - No modifications
- ✅ Product Master - No modifications
- ✅ QC System - No modifications
- ✅ Existing Item Issue functionality - Still works exactly the same
- ✅ All existing database tables - Untouched
- ✅ No refactoring of existing code

### Validation:
- One recipe per finished product (enforced)
- At least one material required
- Quantities must be positive
- Recipe can only be deleted if no dependencies exist

### Permissions:
- Requires write permissions to create/edit/delete recipes
- Follows existing RBAC system

## Testing Checklist

- [ ] Create a new recipe with multiple materials
- [ ] Edit an existing recipe
- [ ] Delete a recipe
- [ ] Use "Issue by Recipe" to auto-populate materials
- [ ] Verify calculated quantities are correct
- [ ] Ensure existing manual item selection still works
- [ ] Check that all sidebar links navigate correctly

## Future Enhancements (Optional)

Potential features for future versions:
- Recipe versioning
- Multi-level BOMs (sub-assemblies)
- Recipe costing
- Recipe duplication
- Batch recipe application
- Recipe export/import

## Support

For issues or questions about this feature:
1. Check that migration script was run: `python migrate_add_bom.py`
2. Verify database tables exist: `bom_recipes`, `bom_recipe_items`
3. Check Flask logs for errors
4. Ensure products exist in Product Master before creating recipes

---

**Version:** 1.0  
**Date:** 2026-08-01  
**Status:** Production Ready
