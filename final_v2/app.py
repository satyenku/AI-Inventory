# app.py
import os
import re
import sqlite3
import time
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify


def parse_decimal(value, fallback=0.0):
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == "":
        return fallback

    # Normalize common grouping / currency formats
    cleaned = re.sub(r"[^0-9.,\-+]", "", text)
    if cleaned.count(".") > 1 and cleaned.count(",") == 0:
        cleaned = cleaned.replace(".", "")
    elif cleaned.count(",") > 0 and cleaned.count(".") > 0:
        cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") > 0:
        cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return fallback
from config import Config
from db_helpers import get_db_connection, log_stock_movement

# Patch PIL font engine (if needed for older Pillow environments)
from PIL import ImageFont
if not hasattr(ImageFont.FreeTypeFont, 'getsize'):
    def _free_type_font_getsize(self, text):
        bbox = self.getbbox(text)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    ImageFont.FreeTypeFont.getsize = _free_type_font_getsize

import barcode
from barcode.writer import ImageWriter
import gemini_extractor as ai

app = Flask(__name__)
app.config.from_object(Config)
Config.validate()

def login_required(f):
    """Simple helper ensuring secure authenticated system routes."""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def generate_barcode_asset(barcode_value):
    folder = os.path.join(app.root_path, 'static', 'barcodes')
    os.makedirs(folder, exist_ok=True)
    code128 = barcode.get('code128', barcode_value, writer=ImageWriter())
    file_path = os.path.join(folder, barcode_value)
    return code128.save(file_path)


def ensure_barcode_asset_exists(barcode_value):
    if not barcode_value:
        return None
    folder = os.path.join(app.root_path, 'static', 'barcodes')
    os.makedirs(folder, exist_ok=True)
    png_path = os.path.join(folder, f"{barcode_value}.png")
    if not os.path.exists(png_path):
        try:
            return generate_barcode_asset(barcode_value)
        except Exception:
            return None
    return png_path

# -------------------------------------------------------------
# ROUTING HANDLERS
# -------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()
        
        # Plain-text alignment corresponding to user initialization structure
        if user and user['password_hash'] == password:
            session['user'] = user['username']
            session['user_id'] = user['id']
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid credentials", "error")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    
    # 1. Read Stock Alarm Counts
    low_stock = conn.execute(
        "SELECT COUNT(*) FROM products WHERE current_stock <= min_stock_level AND min_stock_level > 0"
    ).fetchone()[0]
    
    # 2. Get Product Inventories
    items = conn.execute("SELECT * FROM products ORDER BY item_code ASC").fetchall()
    
    # 3. Read Issued items 
    issues = conn.execute("""
        SELECT i.issue_date, i.issue_slip_no, p.item_code, p.item_name, i.issued_to, ii.quantity, p.unit
        FROM item_issue_items ii
        JOIN item_issues i ON ii.issue_id = i.id
        JOIN products p ON ii.product_id = p.id
        ORDER BY i.created_at DESC LIMIT 10
    """).fetchall()
    
    conn.close()
    return render_template('dashboard.html', low_stock=low_stock, items=items, issues=issues)

@app.route('/products', methods=['GET', 'POST'])
@login_required
def product_master():
    conn = get_db_connection()
    if request.method == 'POST':
        item_code = request.form.get('item_code')
        item_name = request.form.get('item_name')
        category = request.form.get('category')
        subcategory = request.form.get('subcategory')
        unit = request.form.get('unit')
        min_stock = float(request.form.get('min_stock_level') or 0)
        max_stock = float(request.form.get('max_stock_level') or 0)
        reorder_level = float(request.form.get('reorder_level') or 0)
        hsn = request.form.get('hsn_sac_code')
        location = request.form.get('storage_location')
        description = request.form.get('description')
        
        try:
            conn.execute("""
                INSERT INTO products 
                (item_code, barcode, item_name, category, subcategory, unit, min_stock_level, max_stock_level, reorder_level, hsn_sac_code, storage_location, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (item_code, item_code, item_name, category, subcategory, unit, min_stock, max_stock, reorder_level, hsn, location, description))
            conn.commit()
            ensure_barcode_asset_exists(item_code)
            flash("Product registered successfully!", "success")
        except sqlite3.IntegrityError:
            flash("Product Code or Barcode already exists.", "error")
            
        return redirect(url_for('product_master'))
        
    products_list = conn.execute("SELECT * FROM products ORDER BY item_code ASC").fetchall()
    conn.close()
    return render_template('product_master.html', products=products_list)

@app.route('/suppliers', methods=['GET', 'POST'])
@login_required
def supplier_management():
    conn = get_db_connection()
    if request.method == 'POST':
        name = request.form.get('supplier_name')
        contact = request.form.get('contact_person')
        phone = request.form.get('phone')
        email = request.form.get('email')
        address = request.form.get('address')
        
        conn.execute("""
            INSERT INTO suppliers (supplier_name, contact_person, phone, email, address)
            VALUES (?, ?, ?, ?, ?)
        """, (name, contact, phone, email, address))
        conn.commit()
        flash("Supplier record created successfully.", "success")
        return redirect(url_for('supplier_management'))
        
    suppliers = conn.execute("SELECT * FROM suppliers ORDER BY supplier_name ASC").fetchall()
    conn.close()
    return render_template('supplier_management.html', suppliers=suppliers)

@app.route('/item-entry')
@login_required
def item_entry():
    conn = get_db_connection()
    suppliers = conn.execute("SELECT * FROM suppliers").fetchall()
    conn.close()
    return render_template('item_entry.html', suppliers=suppliers)

@app.route('/api/extract', methods=['POST'])
@login_required
def api_extract_data():
    if 'file' not in request.files:
        return {"error": "Missing upload file payload"}, 400
    file = request.files['file']
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(file_path)
    try:
        extracted = ai.extract_invoice_data(file_path)
        return extracted.model_dump()
    except Exception as e:
        return {"error": str(e)}, 500
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

@app.route('/api/save', methods=['POST'])
@login_required
def api_save_invoice():
    data = request.json
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Resolve vendor match
            vendor_name = data.get('vendor_name', 'Unknown')
            cursor.execute("SELECT id FROM suppliers WHERE supplier_name LIKE ?", (f"%{vendor_name}%",))
            sup_row = cursor.fetchone()
            supplier_id = sup_row['id'] if sup_row else None
            
            # Create Invoice entry
            invoice_num = data.get('invoice_number', '').strip()
            if not invoice_num:
                invoice_num = f"UNMAPPED-{int(time.time())}"

            total_amt = parse_decimal(data.get('total_amount'), 0.0)
            cursor.execute("""
                INSERT INTO invoices (vendor_name, invoice_number, invoice_date, supplier_id, total_amount)
                VALUES (?, ?, ?, ?, ?)
            """, (vendor_name, invoice_num, data.get('invoice_date', ''), supplier_id, total_amt))
            invoice_id = cursor.lastrowid
            
            # Create Goods Receipt Entry (GRN)
            grn_no = f"GRN-{invoice_num}"
            cursor.execute("""
                INSERT INTO grn (grn_no, invoice_id, supplier_id, received_date, received_by)
                VALUES (?, ?, ?, ?, ?)
            """, (grn_no, invoice_id, supplier_id, data.get('invoice_date', ''), session.get('user_id')))
            grn_id = cursor.lastrowid
            
            line_items = data.get('line_items', [])
            generated_barcodes = []
            for index, item in enumerate(line_items, start=1):
                desc = item.get('description', 'Generic Item')
                qty = parse_decimal(item.get('qty'), 0.0)
                price = parse_decimal(item.get('unit_price'), 0.0)
                amount = parse_decimal(item.get('amount'), 0.0)
                
                # Check / Resolve matching internal item codes
                cursor.execute("SELECT id FROM products WHERE item_name = ? OR item_code = ?", (desc, desc))
                prod = cursor.fetchone()
                if prod:
                    product_id = prod['id']
                else:
                    item_code = f"AUTO-{re.sub(r'[^A-Z0-9]', '', desc.upper())[:10]}"
                    cursor.execute("""
                        INSERT INTO products (item_code, barcode, item_name, unit, current_stock)
                        VALUES (?, ?, ?, 'Nos', 0)
                    """, (item_code, item_code, desc))
                    product_id = cursor.lastrowid
                    ensure_barcode_asset_exists(item_code)

                # Add Line Item
                cursor.execute("""
                    INSERT INTO invoice_items (invoice_id, product_id, item_name, quantity, unit, unit_price, line_total)
                    VALUES (?, ?, ?, ?, 'Nos', ?, ?)
                """, (invoice_id, product_id, desc, qty, price, amount))
                invoice_item_id = cursor.lastrowid
                
                # GRN specific additions
                cursor.execute("""
                    INSERT INTO grn_items (grn_id, product_id, quantity, unit, unit_price)
                    VALUES (?, ?, ?, 'Nos', ?)
                """, (grn_id, product_id, qty, price))
                grn_item_id = cursor.lastrowid
                
                # Adjust Stock and Record Movement
                log_stock_movement(cursor, product_id, "GRN", "grn_items", grn_item_id, qty)
                
                # Unique barcode per GRN line item — timestamp suffix ensures
                # same product on different invoices gets different barcodes.
                # Allows individual-item returns by scanning barcode.
                unique_suffix = str(int(time.time() * 1000) % 100000 + index)
                barcode_no = f"{invoice_num}-{index}-{unique_suffix}"
                barcode_file_path = generate_barcode_asset(barcode_no)
                barcode_path = os.path.relpath(barcode_file_path, app.root_path)

                cursor.execute("""
                    INSERT INTO barcode_registry (barcode_no, invoice_id, invoice_item_id, barcode_image)
                    VALUES (?, ?, ?, ?)
                """, (barcode_no, invoice_id, invoice_item_id, barcode_path))

                # Product master barcode NOT overwritten — GRN barcodes are
                # per-receipt items, product master keeps its own item_code barcode.
                generated_barcodes.append({
                    "barcode_no": barcode_no,
                    "item_name": desc,
                    "barcode_image": "/" + barcode_path.replace("\\", "/"),
                })
                
        return {"status": "success", "invoice_id": invoice_id, "barcodes": generated_barcodes}, 200
    except Exception as e:
        return {"error": str(e)}, 500

@app.route('/item-issue', methods=['GET', 'POST'])
@login_required
def item_issue():
    if request.method == 'POST':
        slip_no = request.form.get('issue_slip_no')
        issue_date = request.form.get('issue_date')
        issued_to = request.form.get('issued_to')
        work_order = request.form.get('work_order_no')
        
        product_id = int(request.form.get('product_id'))
        qty = float(request.form.get('qty') or 0.0)
        
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO item_issues (issue_slip_no, issue_date, issued_to, work_order_no, issued_by)
                    VALUES (?, ?, ?, ?, ?)
                """, (slip_no, issue_date, issued_to, work_order, session.get('user_id')))
                issue_id = cursor.lastrowid
                
                cursor.execute("""
                    INSERT INTO item_issue_items (issue_id, product_id, quantity, unit)
                    VALUES (?, ?, ?, 'Nos')
                """, (issue_id, product_id, qty))
                issue_item_id = cursor.lastrowid
                
                # Decrement Stock via helper
                log_stock_movement(cursor, product_id, "ISSUE", "item_issue_items", issue_item_id, -qty)
                conn.commit()
                flash("Stock issued successfully.", "success")
        except sqlite3.IntegrityError:
            flash("Issue Slip Number must be unique.", "error")
        except Exception as e:
            flash(f"Error executing transaction: {e}", "error")
        return redirect(url_for('item_issue'))
        
    with get_db_connection() as conn:
        products = conn.execute("SELECT id, item_code, item_name, barcode, current_stock FROM products ORDER BY item_code ASC").fetchall()
    return render_template('item_issue.html', products=products)

@app.route('/inventory-return', methods=['GET', 'POST'])
@login_required
def inventory_return():
    if request.method == 'POST':
        return_id = request.form.get('return_id')
        return_date = request.form.get('return_date')
        returned_by = request.form.get('returned_by')
        dept = request.form.get('department')
        reason = request.form.get('reason')
        product_id = int(request.form.get('product_id'))
        qty = float(request.form.get('qty') or 0)
        condition = request.form.get('condition', 'Good')
        
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO inventory_returns (return_id, return_date, returned_by, department, reason, approved_by)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (return_id, return_date, returned_by, dept, reason, session.get('user_id')))
                ret_id = cursor.lastrowid
                
                cursor.execute("""
                    INSERT INTO inventory_return_items (return_id, product_id, quantity, unit, condition)
                    VALUES (?, ?, ?, 'Nos', ?)
                """, (ret_id, product_id, qty, condition))
                ret_item_id = cursor.lastrowid
                
                # Returns increase stock back
                log_stock_movement(cursor, product_id, "RETURN", "inventory_return_items", ret_item_id, qty)
                conn.commit()
                flash("Return entry logged successfully.", "success")
        except sqlite3.IntegrityError:
            flash("Return ID already exists in database record.", "error")
        except Exception as e:
            flash(f"Error handling entry transaction: {e}", "error")
        return redirect(url_for('inventory_return'))
        
    with get_db_connection() as conn:
        products = conn.execute("SELECT id, item_code, item_name, barcode FROM products ORDER BY item_code ASC").fetchall()
    return render_template('inventory_return.html', products=products)

@app.route('/inventory-status')
@login_required
def inventory_status():
    search_query = request.args.get('search', '').strip()
    product = None
    ledger = []
    
    if search_query:
        conn = get_db_connection()
        product = conn.execute("""
            SELECT * FROM products 
            WHERE item_code = ? OR barcode = ? OR item_name LIKE ?
        """, (search_query, search_query, f"%{search_query}%")).fetchone()
        
        if product:
            ledger = conn.execute("""
                SELECT moved_at, movement_type, reference_table, quantity_change, balance_after
                FROM stock_ledger 
                WHERE product_id = ? 
                ORDER BY moved_at DESC
            """, (product['id'],)).fetchall()
        conn.close()
        
    return render_template('inventory_status.html', product=product, ledger=ledger, query=search_query)


@app.route('/users', methods=['GET', 'POST'])
@login_required
def user_management():
    """User management — admin only."""
    conn = get_db_connection()
    current_role = conn.execute(
        "SELECT role FROM users WHERE username = ?", (session.get('user'),)
    ).fetchone()
    conn.close()

    if not current_role or current_role['role'] != 'admin':
        flash('Access denied. Admin role required.', 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        action = request.form.get('action')
        conn = get_db_connection()

        if action == 'add':
            username  = request.form.get('username', '').strip()
            password  = request.form.get('password', '').strip()
            full_name = request.form.get('full_name', '').strip()
            role      = request.form.get('role', 'staff')
            if not username or not password:
                flash('Username and password are required.', 'error')
                conn.close()
                return redirect(url_for('user_management'))
            try:
                conn.execute(
                    "INSERT INTO users (username, password_hash, full_name, role) VALUES (?,?,?,?)",
                    (username, password, full_name, role)
                )
                conn.commit()
                flash(f"User '{username}' created successfully.", 'success')
            except Exception:
                flash('Username already exists.', 'error')

        elif action == 'toggle':
            uid = request.form.get('user_id')
            conn.execute(
                "UPDATE users SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?",
                (uid,)
            )
            conn.commit()
            flash('User status updated.', 'success')

        elif action == 'reset_password':
            uid      = request.form.get('user_id')
            new_pass = request.form.get('new_password', '').strip()
            if new_pass:
                conn.execute(
                    "UPDATE users SET password_hash=? WHERE id=?",
                    (new_pass, uid)
                )
                conn.commit()
                flash('Password updated.', 'success')

        elif action == 'delete':
            uid = request.form.get('user_id')
            # Prevent deleting the last admin
            remaining = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin' AND id != ?", (uid,)
            ).fetchone()[0]
            if remaining == 0:
                flash('Cannot delete the only admin account.', 'error')
            else:
                conn.execute("DELETE FROM users WHERE id=?", (uid,))
                conn.commit()
                flash('User deleted.', 'success')

        conn.close()
        return redirect(url_for('user_management'))

    conn = get_db_connection()
    users = conn.execute("SELECT * FROM users ORDER BY role DESC, username ASC").fetchall()
    conn.close()
    return render_template('user_management.html', users=users)


@app.route('/api/barcode-lookup')
@login_required
def barcode_lookup():
    """
    Trace a GRN barcode back to its supplier through the chain:
    barcode_registry -> invoice_items -> invoices -> suppliers
    Used by inventory_return page to auto-fill supplier details.
    """
    barcode_no = request.args.get('barcode', '').strip()
    if not barcode_no:
        return jsonify({"error": "No barcode provided"}), 400

    conn = get_db_connection()
    row = conn.execute("""
        SELECT
            br.barcode_no,
            ii.item_name,
            ii.quantity     AS original_qty,
            ii.unit,
            ii.unit_price,
            p.id            AS product_id,
            p.item_code,
            p.item_name     AS product_name,
            p.current_stock,
            inv.invoice_number,
            inv.invoice_date,
            inv.vendor_name,
            s.id            AS supplier_id,
            s.supplier_name,
            s.contact_person,
            s.phone,
            s.email,
            s.address
        FROM barcode_registry br
        JOIN invoice_items ii  ON br.invoice_item_id = ii.id
        JOIN invoices inv      ON br.invoice_id      = inv.id
        LEFT JOIN products p   ON ii.product_id      = p.id
        LEFT JOIN suppliers s  ON inv.supplier_id    = s.id
        WHERE br.barcode_no = ?
    """, (barcode_no,)).fetchone()
    conn.close()

    if not row:
        return jsonify({"error": f"No GRN record found for barcode: {barcode_no}"}), 404

    return jsonify(dict(row))

if __name__ == '__main__':
    app.run(debug=True, port=5000)