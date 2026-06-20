import os
import sqlite3
import csv
import json
import re
from io import StringIO
from dotenv import load_dotenv
load_dotenv()  # Load GEMINI_API_KEY from .env before anything else
from flask import Flask, render_template, request, redirect, url_for, Response
import barcode
from barcode.writer import ImageWriter

import gemini_extractor as ai

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DB_FILE = 'invoices.db'

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    return conn

# ===========================
# BARCODE IMAGE GENERATION
# ===========================
def create_barcode_image(barcode_value):

    folder = os.path.join(app.root_path, 'static', 'barcodes')

    os.makedirs(folder, exist_ok=True)

    code128 = barcode.get(
        'code128',
        barcode_value,
        writer=ImageWriter()
    )

    file_path = os.path.join(folder, barcode_value)

    saved_file = code128.save(file_path)

    return saved_file


def init_and_patch_db():

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS barcode_registry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        barcode_no TEXT NOT NULL UNIQUE,

        invoice_id INTEGER NOT NULL,

        invoice_item_id INTEGER NOT NULL,

        barcode_image TEXT,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY(invoice_id) REFERENCES invoices(id),
        FOREIGN KEY(invoice_item_id) REFERENCES invoice_items(id)
    )
    """)

    conn.commit()
    conn.close()
    
init_and_patch_db()

@app.route('/', methods=['GET'])
def index():
    return render_template('item_entry.html')

@app.route('/api/extract', methods=['POST'])
def api_extract_data():
    if 'file' not in request.files:
        return {"error": "No file uploaded"}, 400
        
    file = request.files['file']
    if file.filename == '':
        return {"error": "Invalid file"}, 400
        
    file_path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(file_path)
    
    try:
        extracted_data = ai.extract_invoice_data(file_path)
        return extracted_data.model_dump()
    except Exception as e:
        return {"error": str(e)}, 500
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

@app.route('/api/save', methods=['POST'])
def api_save_invoice():

    data = request.json

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # -----------------------------------
        # Save Invoice Header
        # -----------------------------------
        cursor.execute(
            """
            INSERT INTO invoices
            (
                vendor_name,
                invoice_number,
                invoice_date,
                total_amount,
                uploaded_at
            )
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (
                data.get('vendor_name', 'Unknown'),
                data.get('invoice_number', ''),
                data.get('invoice_date', ''),
                data.get('total_amount', 0.0)
            )
        )

        invoice_id = cursor.lastrowid

        invoice_number = data.get('invoice_number', '').strip()

        line_items = data.get('line_items', [])

        def safe_float(val):

            if not val:
                return 0.0

            clean_str = re.sub(r'[^\d.-]', '', str(val))

            try:
                return float(clean_str)
            except ValueError:
                return 0.0

        # -----------------------------------
        # Save Items + Generate Barcodes
        # -----------------------------------
        for index, item in enumerate(line_items, start=1):

            qty = safe_float(item.get('qty', 0))
            unit_price = safe_float(item.get('unit_price', 0))
            line_total = safe_float(item.get('amount', 0))

            cursor.execute(
                """
                INSERT INTO invoice_items
                (
                    invoice_id,
                    item_name,
                    quantity,
                    unit,
                    unit_price,
                    line_total
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    item.get('description', ''),
                    qty,
                    item.get('unit', 'Nos'),
                    unit_price,
                    line_total
                )
            )

            invoice_item_id = cursor.lastrowid

            # -----------------------------------
            # Barcode Format
            # Example:
            # INT-001-1
            # INT-001-2
            # INT-001-3
            # -----------------------------------
            barcode_no = f"{invoice_number}-{index}"

            # Create PNG
            create_barcode_image(barcode_no)

            barcode_image_path = (
                f"static/barcodes/{barcode_no}.png"
            )

            # Save Registry
            cursor.execute(
                """
                INSERT INTO barcode_registry
                (
                    barcode_no,
                    invoice_id,
                    invoice_item_id,
                    barcode_image
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    barcode_no,
                    invoice_id,
                    invoice_item_id,
                    barcode_image_path
                )
            )

        conn.commit()
        conn.close()

        return {
            "status": "success",
            "invoice_id": invoice_id,
            "message": "Invoice, items and barcodes saved successfully."
        }, 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "error": str(e)
        }, 500
        
        # 2. Extract selected line_items and map them to invoice_items correctly
        line_items = data.get('line_items', [])
        
        def safe_float(val):
            if not val: return 0.0
            clean_str = re.sub(r'[^\d.-]', '', str(val))
            try: return float(clean_str)
            except ValueError: return 0.0

        for item in line_items:
            # Safely grab fields parsing strings to floats where needed
            qty = safe_float(item.get('qty', 0))
            unit_price = safe_float(item.get('unit_price', 0))
            line_total = safe_float(item.get('amount', 0))
            
            cursor.execute(
                """
                INSERT INTO invoice_items (invoice_id, item_name, quantity, unit, unit_price, line_total)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    item.get('description', ''),
                    qty,
                    item.get('unit', 'Nos'),
                    unit_price,
                    line_total
                )
            )

        conn.commit()
        conn.close()
        return {"status": "success", "invoice_id": invoice_id}, 200
    except Exception as e:
        return {"error": str(e)}, 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)