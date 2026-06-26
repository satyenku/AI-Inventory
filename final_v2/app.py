# app.py
import os
import re
import sqlite3
import time
from datetime import date
from html import escape
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response


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
from db_helpers import get_db_connection, log_stock_movement, insert_product_property

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


def resolve_product_for_qc(cursor, product_id=None, item_name=None):
    if product_id:
        return cursor.execute(
            "SELECT * FROM products WHERE id = ?",
            (product_id,)
        ).fetchone()

    item_name = (item_name or "").strip()
    if not item_name:
        return None

    return cursor.execute("""
        SELECT *
        FROM products
        WHERE item_name = ? OR item_code = ? OR item_name LIKE ?
        ORDER BY
            CASE
                WHEN item_name = ? THEN 0
                WHEN item_code = ? THEN 1
                ELSE 2
            END,
            id
        LIMIT 1
    """, (item_name, item_name, f"%{item_name}%", item_name, item_name)).fetchone()


def excel_col(index):
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def xlsx_cell(row_index, col_index, value="", style_id=0):
    ref = f"{excel_col(col_index)}{row_index}"
    style = f' s="{style_id}"' if style_id else ""
    if value is None:
        value = ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    return (
        f'<c r="{ref}" t="inlineStr"{style}>'
        f'<is><t>{escape(str(value))}</t></is></c>'
    )


def xlsx_row(row_index, values, style_id=0, height=None):
    height_xml = f' ht="{height}" customHeight="1"' if height else ""
    cells = "".join(
        xlsx_cell(row_index, col_index, value, style_id)
        for col_index, value in enumerate(values, start=1)
    )
    return f'<row r="{row_index}"{height_xml}>{cells}</row>'


def build_qc_xlsx(product, specs, meta, observations_by_property=None):
    observations_by_property = observations_by_property or {}
    product_name = product["item_name"] if product else meta.get("item_name", "")
    item_code = product["item_code"] if product else ""
    invoice_no = meta.get("invoice_number", "")
    invoice_date = meta.get("invoice_date", "")
    qty = meta.get("qty", "")
    challan = " / ".join(part for part in [invoice_no, invoice_date] if part)
    report_date = date.today().strftime("%d-%m-%Y")

    rows = [
        xlsx_row(1, ["Galitat", "", "", "INWARD MATERIAL INSPECTION REPORT", "", "", "", "", "", "QES/QA/14", ""], 1, 28),
        xlsx_row(2, ["Date:", report_date, "", "", "", "", "", "", "", "02/01.03.2025", ""], 2, 22),
        xlsx_row(3, ["Material Recd.as per RCIA No. :", "", "", "", "", "", "Invoice / Challan No. & Date :", challan, "", "", ""], 2, 24),
        xlsx_row(4, ["Part No. :", item_code, "", "", "", "", "Qty Recd:", qty, "", "", ""], 2, 24),
        xlsx_row(5, ["Description:", product_name, "", "", "", "", "Sampling QTY:", "", "", "", ""], 2, 24),
        xlsx_row(6, [""] * 11, 0, 8),
        xlsx_row(7, ["SR\nNO.", "SPECIFICATION FOR\nCRITICAL DIMENSION", "SPECIFICATION", "", "", "OBSERVATIONS", "", "", "", "", "REMARKS"], 3, 36),
        xlsx_row(8, ["", "", "MIN", "MAX", "METHOD", "1", "2", "3", "4", "5", ""], 3, 28),
    ]

    current_row = 9
    for index, spec in enumerate(specs, start=1):
        min_value = "" if spec["min_value"] is None else spec["min_value"]
        max_value = "" if spec["max_value"] is None else spec["max_value"]
        saved_obs = observations_by_property.get(spec["id"], {})
        rows.append(xlsx_row(current_row, [
            index,
            spec["property_name"] or "",
            min_value,
            max_value,
            spec["method"] or "",
            saved_obs.get("obs1", ""),
            saved_obs.get("obs2", ""),
            saved_obs.get("obs3", ""),
            saved_obs.get("obs4", ""),
            saved_obs.get("obs5", ""),
            saved_obs.get("remarks", ""),
        ], 4, 32))
        current_row += 1

    if not specs:
        rows.append(xlsx_row(current_row, [
            "", "No QC specifications found in product_properties.", "", "", "", "", "", "", "", "", ""
        ], 4, 28))

    merge_refs = [
        "A1:C1", "D1:I1", "J1:K1",
        "B2:C2", "J2:K2",
        "A3:F3", "H3:K3",
        "B4:F4", "H4:K4",
        "B5:F5", "H5:K5",
        "A7:A8", "B7:B8", "C7:E7", "F7:J7", "K7:K8",
    ]
    merges = "".join(f'<mergeCell ref="{ref}"/>' for ref in merge_refs)

    sheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <sheetViews><sheetView workbookViewId="0"/></sheetViews>
    <sheetFormatPr defaultRowHeight="18"/>
    <cols>
        <col min="1" max="1" width="8" customWidth="1"/>
        <col min="2" max="2" width="30" customWidth="1"/>
        <col min="3" max="4" width="12" customWidth="1"/>
        <col min="5" max="5" width="20" customWidth="1"/>
        <col min="6" max="10" width="11" customWidth="1"/>
        <col min="11" max="11" width="22" customWidth="1"/>
    </cols>
    <sheetData>{''.join(rows)}</sheetData>
    <mergeCells count="{len(merge_refs)}">{merges}</mergeCells>
</worksheet>"""

    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
    <fonts count="3">
        <font><sz val="11"/><name val="Calibri"/></font>
        <font><b/><sz val="14"/><name val="Calibri"/></font>
        <font><b/><sz val="11"/><name val="Calibri"/></font>
    </fonts>
    <fills count="3">
        <fill><patternFill patternType="none"/></fill>
        <fill><patternFill patternType="gray125"/></fill>
        <fill><patternFill patternType="solid"><fgColor rgb="FFD9EAF7"/><bgColor indexed="64"/></patternFill></fill>
    </fills>
    <borders count="2">
        <border><left/><right/><top/><bottom/><diagonal/></border>
        <border>
            <left style="thin"><color auto="1"/></left>
            <right style="thin"><color auto="1"/></right>
            <top style="thin"><color auto="1"/></top>
            <bottom style="thin"><color auto="1"/></bottom>
            <diagonal/>
        </border>
    </borders>
    <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
    <cellXfs count="5">
        <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
        <xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
        <xf numFmtId="0" fontId="2" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="center" wrapText="1"/></xf>
        <xf numFmtId="0" fontId="2" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
        <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    </cellXfs>
    <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <sheets><sheet name="QC Report" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
    <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml" ContentType="application/xml"/>
    <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
    <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
    <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    return output.getvalue()

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
            cur = conn.execute("""
                INSERT INTO products 
                (item_code, barcode, item_name, category, subcategory, unit, min_stock_level, max_stock_level, reorder_level, hsn_sac_code, storage_location, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (item_code, item_code, item_name, category, subcategory, unit, min_stock, max_stock, reorder_level, hsn, location, description))
            product_id = cur.lastrowid

            # Persist any inspection properties supplied in the form
            prop_names = request.form.getlist('property_name[]')
            prop_mins = request.form.getlist('property_min[]')
            prop_maxs = request.form.getlist('property_max[]')
            prop_methods = request.form.getlist('property_method[]')

            cursor = conn.cursor()
            for idx, name in enumerate(prop_names):
                name = (name or '').strip()
                if not name:
                    continue
                try:
                    min_val = float(prop_mins[idx]) if idx < len(prop_mins) and prop_mins[idx] not in (None, '') else None
                except Exception:
                    min_val = None
                try:
                    max_val = float(prop_maxs[idx]) if idx < len(prop_maxs) and prop_maxs[idx] not in (None, '') else None
                except Exception:
                    max_val = None
                method = prop_methods[idx] if idx < len(prop_methods) else None
                insert_product_property(cursor, product_id, name, min_val, max_val, method)

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
            
            # Validate supplier is provided and exists
            supplier_id_raw = data.get('supplier_id')
            if not supplier_id_raw:
                return {"error": "Supplier is required. Please select a supplier from the dropdown before proceeding."}, 400
            
            # Convert to integer
            try:
                supplier_id = int(supplier_id_raw)
            except (ValueError, TypeError):
                return {"error": f"Invalid supplier ID format: {supplier_id_raw}"}, 400
            
            cursor.execute("SELECT id, supplier_name FROM suppliers WHERE id = ?", (supplier_id,))
            supplier_row = cursor.fetchone()
            if not supplier_row:
                return {"error": f"Supplier ID {supplier_id} is not found in the system. Please add the supplier first."}, 400
            
            # Create Invoice entry with validated supplier_id
            vendor_name = data.get('vendor_name', 'Unknown')
            invoice_num = data.get('invoice_number', '').strip()
            if not invoice_num:
                invoice_num = f"UNMAPPED-{int(time.time())}"

            total_amt = parse_decimal(data.get('total_amount'), 0.0)
            cursor.execute("""
                INSERT INTO invoices (vendor_name, invoice_number, invoice_date, supplier_id, total_amount)
                VALUES (?, ?, ?, ?, ?)
            """, (vendor_name, invoice_num, data.get('invoice_date', ''), supplier_id, total_amt))
            invoice_id = cursor.lastrowid
            
            # Create Goods Receipt Entry (GRN) with validated supplier_id
            grn_no = f"GRN-{invoice_num}"
            received_by_id = session.get('user_id')
            if not received_by_id:
                return {"error": "User session is invalid. Please log in again."}, 401
            
            try:
                received_by_id = int(received_by_id)
            except (ValueError, TypeError):
                return {"error": "Invalid user session."}, 401
            
            cursor.execute("""
                INSERT INTO grn (grn_no, invoice_id, supplier_id, received_date, received_by)
                VALUES (?, ?, ?, ?, ?)
            """, (grn_no, invoice_id, supplier_id, data.get('invoice_date', ''), received_by_id))
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


# =====================================================
# INSPECTION ENTRY PAGE
# =====================================================

@app.route('/inspection_entry')
@login_required
def inspection_entry():
    return render_template('inspection_entry.html')


# =====================================================
# LOAD PRODUCTS DROPDOWN
# =====================================================

@app.route('/api/products')
@login_required
def api_products():

    conn = get_db_connection()

    products = conn.execute("""
        SELECT id, item_name
        FROM products
        ORDER BY item_name
    """).fetchall()

    conn.close()

    return jsonify({
        "products": [
            {
                "id": product["id"],
                "item_name": product["item_name"]
            }
            for product in products
        ]
    })


# =====================================================
# LOAD PRODUCT PROPERTIES
# =====================================================

@app.route('/api/product-properties/<int:product_id>')
@login_required
def api_product_properties(product_id):

    conn = get_db_connection()

    rows = conn.execute("""
        SELECT
            id,
            property_name,
            min_value,
            max_value,
            method
        FROM product_properties
        WHERE product_id = ?
        ORDER BY id
    """, (product_id,)).fetchall()

    conn.close()

    return jsonify({
        "properties": [
            {
                "id": row["id"],
                "property_name": row["property_name"],
                "min_value": row["min_value"],
                "max_value": row["max_value"],
                "method": row["method"]
            }
            for row in rows
        ]
    })


# =====================================================
# SAVE INSPECTION
# =====================================================

@app.route('/api/save-inspection', methods=['POST'])
@login_required
def save_inspection():

    data = request.json

    try:

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO inspection_entries
            (
                product_id,
                inspection_date
            )
            VALUES (?, ?)
        """, (
            data.get("product_id"),
            data.get("inspection_date")
        ))

        inspection_id = cursor.lastrowid

        details = data.get("details", [])

        for detail in details:

            cursor.execute("""
                INSERT INTO inspection_details
                (
                    inspection_id,
                    product_property_id,
                    obs1,
                    obs2,
                    obs3,
                    obs4,
                    obs5,
                    remarks
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                inspection_id,
                detail.get("product_property_id"),
                detail.get("obs1"),
                detail.get("obs2"),
                detail.get("obs3"),
                detail.get("obs4"),
                detail.get("obs5"),
                detail.get("remarks")
            ))

        conn.commit()
        conn.close()

        return jsonify({
            "status": "success",
            "message": "Inspection saved successfully."
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/qc-sheet')
def qc_sheet():
    product_id = request.args.get('product_id')
    return render_template("qc_sheet.html")


@app.route('/qc-sheet/excel')
@login_required
def qc_sheet_excel():
    product_id = request.args.get('product_id', type=int)
    item_name = request.args.get('item_name', '').strip()
    meta = {
        "item_name": item_name,
        "invoice_number": request.args.get('invoice_number', '').strip(),
        "invoice_date": request.args.get('invoice_date', '').strip(),
        "qty": request.args.get('qty', '').strip(),
    }

    conn = get_db_connection()
    try:
        product = resolve_product_for_qc(conn, product_id=product_id, item_name=item_name)
        if not product:
            return "No matching product found in Product Master for this extracted item.", 404

        specs = conn.execute("""
            SELECT id, property_name, min_value, max_value, method
            FROM product_properties
            WHERE product_id = ?
            ORDER BY id
        """, (product["id"],)).fetchall()

        latest_inspection = conn.execute("""
            SELECT id
            FROM inspection_entries
            WHERE product_id = ?
            ORDER BY inspection_date DESC, created_at DESC, id DESC
            LIMIT 1
        """, (product["id"],)).fetchone()

        observations_by_property = {}
        if latest_inspection:
            observation_rows = conn.execute("""
                SELECT product_property_id, obs1, obs2, obs3, obs4, obs5, remarks
                FROM inspection_details
                WHERE inspection_id = ?
            """, (latest_inspection["id"],)).fetchall()
            observations_by_property = {
                row["product_property_id"]: {
                    "obs1": row["obs1"] or "",
                    "obs2": row["obs2"] or "",
                    "obs3": row["obs3"] or "",
                    "obs4": row["obs4"] or "",
                    "obs5": row["obs5"] or "",
                    "remarks": row["remarks"] or "",
                }
                for row in observation_rows
            }
    finally:
        conn.close()

    workbook = build_qc_xlsx(product, specs, meta, observations_by_property)
    safe_code = re.sub(r"[^A-Za-z0-9_-]+", "_", product["item_code"] or product["item_name"]).strip("_")
    filename = f"QC_Sheet_{safe_code or product['id']}.xlsx"

    return Response(
        workbook,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.route('/api/qc-sheet/<int:product_id>')
def qc_data(product_id):
    conn = get_db_connection()
    product = conn.execute(
        "SELECT item_name FROM products WHERE id = ?",
        (product_id,)
    ).fetchone()
    specs = conn.execute("""
        SELECT id, property_name, min_value, max_value, method
        FROM product_properties
        WHERE product_id = ?
        ORDER BY id
    """, (product_id,)).fetchall()
    conn.close()

    return jsonify({
        "product_name": product["item_name"] if product else "",
        "specs": [dict(s) for s in specs]
    })
    


if __name__ == '__main__':
    app.run(debug=True, port=5000)
