import sqlite3
from pathlib import Path
from config import Config

conn = sqlite3.connect(Config.DATABASE_PATH)
cur = conn.cursor()

supplier_name = 'Wipro Vendor Pvt Ltd'
cur.execute('SELECT id FROM suppliers WHERE supplier_name = ?', (supplier_name,))
row = cur.fetchone()
if row:
    supplier_id = row[0]
    print(f'Supplier already exists: {supplier_name} (id={supplier_id})')
else:
    cur.execute(
        'INSERT INTO suppliers (supplier_name, contact_person, phone, email, address) VALUES (?, ?, ?, ?, ?)',
        (supplier_name, 'QC Supervisor', '9876543210', 'qc@wipro.com', 'Wipro Campus, Bangalore')
    )
    supplier_id = cur.lastrowid
    print(f'Inserted supplier: {supplier_name} (id={supplier_id})')

products = [
    {
        'item_code': 'WIP-LED-9W',
        'barcode': 'WIP-LED-9W',
        'item_name': 'Wipro LED Bulb 9W',
        'category': 'Electrical',
        'subcategory': 'Lighting',
        'unit': 'Nos',
        'current_stock': 100,
        'properties': [
            ('Lumen Output (lm)', 800, 850, 'Photometer'),
            ('Color Temperature (K)', 4000, 4200, 'Spectrometer'),
            ('Power Consumption (W)', 8.5, 9.5, 'Wattmeter'),
        ]
    },
    {
        'item_code': 'WIP-SW-6A',
        'barcode': 'WIP-SW-6A',
        'item_name': 'Wipro Switch 6A',
        'category': 'Electrical',
        'subcategory': 'Switchgear',
        'unit': 'Nos',
        'current_stock': 200,
        'properties': [
            ('Contact Resistance (mΩ)', 0.1, 0.3, 'Micro-ohm Meter'),
            ('Dielectric Strength (V)', 1500, 2000, 'Hi-Pot'),
            ('Mechanical Life (cycles)', 10000, 20000, 'Cycle Tester'),
        ]
    },
    {
        'item_code': 'WIP-LED-15W',
        'barcode': 'WIP-LED-15W',
        'item_name': 'Wipro LED Bulb 15W',
        'category': 'Electrical',
        'subcategory': 'Lighting',
        'unit': 'Nos',
        'current_stock': 150,
        'properties': [
            ('Lumen Output (lm)', 1300, 1500, 'Photometer'),
            ('Power Consumption (W)', 14.5, 15.5, 'Wattmeter'),
            ('Beam Angle (°)', 110, 120, 'Goniophotometer'),
        ]
    },
]

for product in products:
    cur.execute('SELECT id FROM products WHERE item_code = ?', (product['item_code'],))
    row = cur.fetchone()
    if row:
        product_id = row[0]
        print(f'Product already exists: {product["item_code"]} (id={product_id})')
    else:
        cur.execute(
            'INSERT INTO products (item_code, barcode, item_name, category, subcategory, unit, current_stock) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (
                product['item_code'],
                product['barcode'],
                product['item_name'],
                product['category'],
                product['subcategory'],
                product['unit'],
                product['current_stock'],
            )
        )
        product_id = cur.lastrowid
        print(f'Inserted product: {product["item_code"]} (id={product_id})')

    for prop_name, min_val, max_val, method in product['properties']:
        cur.execute(
            'SELECT id FROM product_properties WHERE product_id = ? AND property_name = ?',
            (product_id, prop_name)
        )
        if cur.fetchone():
            print(f'  Property already exists: {prop_name}')
            continue
        cur.execute(
            'INSERT INTO product_properties (product_id, property_name, min_value, max_value, method) VALUES (?, ?, ?, ?, ?)',
            (product_id, prop_name, min_val, max_val, method)
        )
        print(f'  Inserted property: {prop_name}')

conn.commit()
conn.close()
print('QC test data seeding complete.')
