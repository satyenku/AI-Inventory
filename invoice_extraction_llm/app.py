import os
import sqlite3
import csv
import json
from io import StringIO
from dotenv import load_dotenv
load_dotenv()  # Load GEMINI_API_KEY from .env before anything else
from flask import Flask, render_template, request, redirect, url_for, Response

import gemini_extractor as ai

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DB_FILE = 'invoices.db'

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    return conn

def init_and_patch_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT,
            invoice_date TEXT,
            vendor_name TEXT,
            tax_details TEXT,
            total_amount REAL,
            processed_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_and_patch_db()

@app.route('/', methods=['GET', 'POST'])
def upload_page():
    if request.method == 'POST':
        if 'invoice_file' not in request.files:
            return "No file layer discovered", 400
            
        file = request.files['invoice_file']
        if file.filename == '':
            return "No valid file selected", 400
            
        if file:
            file_path = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(file_path)
            
            try:
                extracted_data = ai.extract_invoice_data(file_path)
                
                invoice_num = extracted_data.invoice_number
                invoice_date = extracted_data.invoice_date
                vendor_name = extracted_data.vendor_name
                
                # Pure tax amount row string
                tax_field = extracted_data.tax_amount_clean
                
                # Safely embed line items using JSON structures to bypass raw pipe splits
                items_list = [item.model_dump() for item in extracted_data.line_items]
                combined_field_payload = f"{tax_field}||JSON_ITEMS:{json.dumps(items_list)}"
                
                total_amount = extracted_data.total_amount
                
            except Exception as e:
                # Clean up the temp file before returning error
                if os.path.exists(file_path):
                    os.remove(file_path)
                # Return a user-facing error page — do NOT write bad data to DB
                error_msg = str(e)
                return render_template('error.html', error=error_msg), 503
            
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO invoices (invoice_number, invoice_date, vendor_name, tax_details, total_amount, processed_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (invoice_num, invoice_date, vendor_name, combined_field_payload, total_amount)
                )
                conn.commit()
                conn.close()
            except Exception as db_err:
                return f"Database Storage Error: {str(db_err)}", 500
            finally:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    
            return redirect(url_for('search_page'))

                
    return render_template('upload.html')

@app.route('/search', methods=['GET'])
def search_page():
    inv_num = request.args.get('invoice_number', '').strip()
    v_name = request.args.get('vendor_name', '').strip()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if inv_num or v_name:
        query = "SELECT * FROM invoices WHERE invoice_number LIKE ? AND vendor_name LIKE ? ORDER BY id DESC"
        cursor.execute(query, (f"%{inv_num}%", f"%{v_name}%"))
    else:
        cursor.execute("SELECT * FROM invoices ORDER BY id DESC")
        
    results = cursor.fetchall()
    conn.close()
    
    # Parse each row's tax_details field to extract tax string and line items
    parsed_invoices = []
    for row in results:
        row_data = {
            'id': row[0],
            'invoice_number': row[1],
            'invoice_date': row[2],
            'vendor_name': row[3],
            'raw_tax_field': row[4] if row[4] else "",
            'total_amount': row[5],
            'processed_at': row[6],
            'tax_display': '',
            'line_items': [],
            'is_modern_format': False,
        }
        
        raw = row_data['raw_tax_field']
        if '||JSON_ITEMS:' in raw:
            parts = raw.split('||JSON_ITEMS:', 1)
            row_data['tax_display'] = parts[0]
            row_data['is_modern_format'] = True
            try:
                row_data['line_items'] = json.loads(parts[1])
            except (json.JSONDecodeError, IndexError):
                row_data['line_items'] = []
        elif '---' in raw:
            row_data['tax_display'] = raw.split('---')[0]
        else:
            row_data['tax_display'] = raw
        
        parsed_invoices.append(row_data)
        
    return render_template('search.html', invoices=parsed_invoices)

@app.route('/export', methods=['GET'])
def export_excel():
    inv_num = request.args.get('invoice_number', '').strip()
    v_name = request.args.get('vendor_name', '').strip()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if inv_num or v_name:
        query = "SELECT id, invoice_number, invoice_date, vendor_name, total_amount, processed_at FROM invoices WHERE invoice_number LIKE ? AND vendor_name LIKE ? ORDER BY id DESC"
        cursor.execute(query, (f"%{inv_num}%", f"%{v_name}%"))
    else:
        cursor.execute("SELECT id, invoice_number, invoice_date, vendor_name, total_amount, processed_at FROM invoices ORDER BY id DESC")
        
    records = cursor.fetchall()
    conn.close()
    
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['System ID', 'Invoice Number', 'Invoice Date', 'Vendor Corporate Name', 'Total Amount ($)', 'Processing Timestamp'])
    for row in records:
        cw.writerow([row[0], row[1], row[2], row[3], f"{row[4]:.2f}", row[5]])
    
    response = Response(si.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=invoice_archive_export.csv'
    return response

if __name__ == '__main__':
    app.run(debug=True, port=5000)