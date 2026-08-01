# migrate_add_bom.py
# Migration script to add BOM (Bill of Materials) tables to existing database

import sqlite3
from config import Config

def migrate_add_bom():
    """Add BOM tables to existing database"""
    conn = sqlite3.connect(Config.DATABASE_PATH)
    cursor = conn.cursor()
    
    # Check if tables already exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bom_recipes'")
    if cursor.fetchone():
        print("BOM tables already exist. Skipping migration.")
        conn.close()
        return
    
    print("Adding BOM tables...")
    
    # Create BOM tables
    cursor.executescript("""
        -- ============================================================
        -- PRODUCT RECIPE / BILL OF MATERIALS (BOM)
        -- ============================================================
        CREATE TABLE IF NOT EXISTS bom_recipes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            finished_product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            recipe_name     TEXT,
            description     TEXT,
            created_by      INTEGER REFERENCES users(id),
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bom_recipe_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id       INTEGER NOT NULL REFERENCES bom_recipes(id) ON DELETE CASCADE,
            raw_material_id INTEGER NOT NULL REFERENCES products(id),
            quantity_required REAL NOT NULL DEFAULT 0,
            unit            TEXT NOT NULL,
            notes           TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_bom_recipes_product ON bom_recipes(finished_product_id);
        CREATE INDEX IF NOT EXISTS idx_bom_items_recipe ON bom_recipe_items(recipe_id);
    """)
    
    conn.commit()
    conn.close()
    print("BOM tables created successfully!")

if __name__ == '__main__':
    migrate_add_bom()
