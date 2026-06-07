import os
import csv
import sqlite3
from io import StringIO
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response
from config import Config
from extractor import extract_text_from_file, parse_invoice_data

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = "super_secure_development_key"

# Ensure the upload destination folder exists on disk
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    """Checks if the uploaded file has a valid extension (PDF, PNG, JPG, JPEG)."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def get_db_connection():
    """Establishes a clean connection to the SQLite database with row factory enabled."""
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row  # Access columns by name like a dict
    return conn

# ==========================================
# ROUTE 1: Document Upload & OCR Ingestion Engine
# ==========================================
@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        # Safety Check: Check if file payload exists in request headers
        if 'invoice_file' not in request.files:
            flash('No file part block detected in request headers.', 'error')
            return redirect(request.url)
            
        file = request.files['invoice_file']
        
        # Safety Check: Check if user submitted an empty selection
        if file.filename == '':
            flash('No document selected for upload processing.', 'error')
            return redirect(request.url)
            
        if file and allowed_file(file.filename):
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(file_path)
            
            try:
                # 1. Fire up the custom extraction/OCR workflow engine
                raw_text = extract_text_from_file(file_path)
                parsed_results = parse_invoice_data(raw_text)
                
                # 2. Database Layer: Safely parse numeric float representations
                try:
                    tax_val = float(parsed_results["tax_amount"])
                except ValueError:
                    tax_val = 0.00
                    
                try:
                    total_val = float(parsed_results["total_amount"])
                except ValueError:
                    total_val = 0.00
                
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO invoices (vendor_name, invoice_number, invoice_date, tax_amount, total_amount, file_name)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    parsed_results["vendor_name"],
                    parsed_results["invoice_number"],
                    parsed_results["invoice_date"],
                    tax_val,
                    total_val,
                    file.filename
                ))
                conn.commit()
                conn.close()
                
                flash('Invoice analyzed, parsed, and stored into database successfully!', 'success')
                return render_template('upload.html', data=parsed_results, preview=raw_text[:1200])
                
            except Exception as e:
                # Catches fallback issues cleanly
                flash(f"Processing Workflow Error: {str(e)}", 'error')
                return render_template('upload.html', data=None, preview=None)
        else:
            flash('Invalid file formatting type. Please upload a valid image or native PDF.', 'error')
            return redirect(request.url)
            
    return render_template('upload.html', data=None, preview=None)

# ==========================================
# CORE FILTERING LOGIC (SOW Query and Retrieval)
# ==========================================
def build_dashboard_query(request_args):
    query = 'SELECT * FROM invoices WHERE 1=1'
    params = []
    
    vendor = request_args.get('vendor_name', '').strip()
    if vendor:
        query += ' AND vendor_name LIKE ?'
        params.append(f'%{vendor}%')
        
    invoice_num = request_args.get('invoice_number', '').strip()
    if invoice_num:
        query += ' AND invoice_number LIKE ?'
        params.append(f'%{invoice_num}%')
        
    start_date = request_args.get('start_date', '').strip()
    if start_date:
        query += ' AND invoice_date >= ?'
        params.append(start_date)
        
    end_date = request_args.get('end_date', '').strip()
    if end_date:
        query += ' AND invoice_date <= ?'
        params.append(end_date)
        
    min_amount = request_args.get('min_amount', '').strip()
    if min_amount:
        try:
            query += ' AND total_amount >= ?'
            params.append(float(min_amount))
        except ValueError:
            pass
            
    max_amount = request_args.get('max_amount', '').strip()
    if max_amount:
        try:
            query += ' AND total_amount <= ?'
            params.append(float(max_amount))
        except ValueError:
            pass
        
    query += ' ORDER BY invoice_date DESC, uploaded_at DESC'
    return query, params

# ==========================================
# ROUTE 2: History Log Audit Trail View with Analytics Panel
# ==========================================
@app.route('/dashboard')
def dashboard():
    """Queries SQLite to serve past invoice extraction logs with summary metrics."""
    query, params = build_dashboard_query(request.args)
    conn = get_db_connection()
    invoices = conn.execute(query, params).fetchall()
    conn.close()
    
    # Calculate real-time summary metrics on active search results
    total_invoices = len(invoices)
    gross_spend = sum(row['total_amount'] for row in invoices)
    total_tax = sum(row['tax_amount'] for row in invoices)
    
    metrics = {
        "total_invoices": total_invoices,
        "gross_spend": gross_spend,
        "total_tax": total_tax
    }
    
    return render_template('dashboard.html', invoices=invoices, filters=request.args, metrics=metrics)

# ==========================================
# ROUTE 3: CSV Export Functionality (Pandas Powered)
# ==========================================
@app.route('/export')
def export_csv():
    """Exports filtered invoice logs to a clean CSV download using Pandas."""
    query, params = build_dashboard_query(request.args)
    conn = get_db_connection()
    
    # Use Pandas to read SQL and convert directly to CSV for compliance
    import pandas as pd
    try:
        df = pd.read_sql_query(query, conn, params=params)
    except Exception as e:
        conn.close()
        flash(f"Export failed: {str(e)}", 'error')
        return redirect(url_for('dashboard'))
    conn.close()
    
    # Rename headers to be user-friendly
    header_mapping = {
        'id': 'Record ID',
        'vendor_name': 'Vendor Entity',
        'invoice_number': 'Invoice Number',
        'invoice_date': 'Document Date',
        'tax_amount': 'Tax/VAT Amount ($)',
        'total_amount': 'Total Value ($)',
        'file_name': 'Source File Reference',
        'uploaded_at': 'System Upload Timestamp'
    }
    df = df.rename(columns=header_mapping)
    
    # Format CSV in memory
    csv_buffer = StringIO()
    df.to_csv(csv_buffer, index=False)
    
    return Response(
        csv_buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=invoice_audit_export.csv"}
    )

# ==========================================
# ROUTE 4: Backend Developer JSON API Endpoint
# ==========================================
@app.route('/api/invoices', methods=['GET'])
def api_get_all_invoices():
    """Returns a clean JSON array stream of all invoices for backend integrations."""
    conn = get_db_connection()
    invoices = conn.execute('SELECT * FROM invoices ORDER BY invoice_date DESC, uploaded_at DESC').fetchall()
    conn.close()
    
    output = []
    for inv in invoices:
        output.append({
            "id": inv["id"],
            "vendor_name": inv["vendor_name"],
            "invoice_number": inv["invoice_number"],
            "invoice_date": inv["invoice_date"],
            "tax_amount": inv["tax_amount"],
            "total_amount": inv["total_amount"],
            "file_name": inv["file_name"],
            "uploaded_at": inv["uploaded_at"]
        })
    return jsonify(output)

if __name__ == '__main__':
    # Run on Port 5050 to bypass potential native port conflicts on Windows
    app.run(debug=True, port=5050)