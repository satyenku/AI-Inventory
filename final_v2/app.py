# app.py
import os
import re
import sqlite3
import time
from datetime import date
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html import escape
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response, send_file
import traceback


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


def normalize_item_text(value):
    return " ".join(str(value or "").strip().lower().split())

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



SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

SMTP_USERNAME = os.getenv("EMAIL_USER")
SMTP_PASSWORD = os.getenv("EMAIL_PASS")

print("SMTP_USERNAME =", SMTP_USERNAME)
print("SMTP_PASSWORD =", SMTP_PASSWORD)

# ==========================================
# LIVE SMTP EMAIL CONFIGURATION
# ==========================================
SMTP_SERVER = "smtp.gmail.com"             
SMTP_PORT = 587
SMTP_USERNAME = os.getenv("EMAIL_USER")     # Outbound business email address
SMTP_PASSWORD = os.getenv("EMAIL_PASS")     # Secure App Password (16 characters)

def send_recovery_email(target_email, username, reset_link):
    """Dispatches an HTML transactional recovery link to the user's verified address."""
    msg = MIMEMultipart()
    msg['From'] = SMTP_USERNAME
    msg['To'] = target_email
    msg['Subject'] = f"Password Reset Request for {username}"

    body = f"""
    <div style="font-family: sans-serif; padding: 20px; color: #333; max-width: 600px; border: 1px solid #e5e7eb; border-radius: 8px;">
        <h3 style="color: #111827;">Hello {username},</h3>
        <p>We received a request to reset your application password. Click the secure link below to set a new password:</p>
        <p style="margin: 25px 0;">
            <a href="{reset_link}" style="background-color: #2563eb; color: white; padding: 10px 18px; text-decoration: none; border-radius: 5px; display: inline-block; font-weight: bold;">Reset Password</a>
        </p>
        <p>This recovery channel is confidential and will automatically expire in 15 minutes.</p>
        <p style="color: #6b7280; font-size: 0.85em; margin-top: 20px; border-top: 1px solid #f3f4f6; padding-top: 10px;">
            If you did not make this request, you can safely ignore this automated message.
        </p>
    </div>
    """
    msg.attach(MIMEText(body, 'html'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()  # Initialize safe Transport Layer Security channel
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"[MAIL SERVER ERROR] Failed email delivery: {e}")
        return False

def login_required(f):
    """Simple helper ensuring secure authenticated system routes."""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Ensure ledger integrity triggers and indexes exist (safe to run multiple times)
def ensure_ledger_integrity():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # unique index to prevent accidental duplicate ledger entries for same source
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_ref_unique ON stock_ledger(reference_table, reference_id, movement_type);")

        # trigger to remove stock_ledger entries when grn_items deleted
        trigger_name = 'trg_delete_grn_items_ledger'
        cur.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name = ?", (trigger_name,))
        if not cur.fetchone():
            cur.execute(f"CREATE TRIGGER {trigger_name} AFTER DELETE ON grn_items BEGIN DELETE FROM stock_ledger WHERE reference_table = 'grn_items' AND reference_id = OLD.id; END;")

        # trigger to remove stock_ledger entries when item_issue_items deleted
        trig2 = 'trg_delete_issue_items_ledger'
        cur.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name = ?", (trig2,))
        if not cur.fetchone():
            cur.execute(f"CREATE TRIGGER {trig2} AFTER DELETE ON item_issue_items BEGIN DELETE FROM stock_ledger WHERE reference_table = 'item_issue_items' AND reference_id = OLD.id; END;")

        # trigger to remove stock_ledger entries when inventory_return_items deleted
        trig3 = 'trg_delete_return_items_ledger'
        cur.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name = ?", (trig3,))
        if not cur.fetchone():
            cur.execute(f"CREATE TRIGGER {trig3} AFTER DELETE ON inventory_return_items BEGIN DELETE FROM stock_ledger WHERE reference_table = 'inventory_return_items' AND reference_id = OLD.id; END;")

        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


ensure_ledger_integrity()

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

    normalized = normalize_item_text(item_name)
    return cursor.execute("""
        SELECT *
        FROM products
        WHERE LOWER(item_name) = ?
           OR LOWER(item_code) = ?
           OR LOWER(item_name) LIKE ?
           OR LOWER(item_code) LIKE ?
        ORDER BY
            CASE
                WHEN LOWER(item_name) = ? THEN 0
                WHEN LOWER(item_code) = ? THEN 1
                ELSE 2
            END,
            CASE WHEN status = 'Active' THEN 0 ELSE 1 END,
            COALESCE(current_stock, 0) DESC,
            id
        LIMIT 1
    """, (normalized, normalized, f"%{normalized}%", f"%{normalized}%", normalized, normalized)).fetchone()


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

        normalized_name = normalize_item_text(item_name)
        existing_product = conn.execute(
            "SELECT id FROM products WHERE LOWER(item_code) = ? OR LOWER(item_name) = ? LIMIT 1",
            (item_code.strip().lower(), normalized_name)
        ).fetchone()
        if existing_product:
            flash("Product Code or Product Name already exists.", "error")
            conn.close()
            return redirect(url_for('product_master'))

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
        name = request.form.get('supplier_name', '').strip()
        gst_number = request.form.get('gst_number', '').strip().upper()
        contact = request.form.get('contact_person', '').strip()
        phone = request.form.get('phone', '').strip()
        email = request.form.get('email', '').strip()
        address = request.form.get('address', '').strip()

        try:
            conn.execute("""
                INSERT INTO suppliers
                (supplier_name, gst_number, contact_person, phone, email, address)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                name,
                gst_number,
                contact,
                phone,
                email,
                address
            ))

            conn.commit()
            flash("Supplier record created successfully.", "success")

        except sqlite3.IntegrityError:
            # Duplicate GST Number
            flash("GST Number already exists.", "error")

        finally:
            conn.close()

        return redirect(url_for('supplier_management'))

    suppliers = conn.execute("""
        SELECT *
        FROM suppliers
        ORDER BY supplier_name ASC
    """).fetchall()

    conn.close()

    return render_template(
        'supplier_management.html',
        suppliers=suppliers
    )

@app.route('/departments', methods=['GET', 'POST'])
@login_required
def department_management():
    conn = get_db_connection()
    if request.method == 'POST':
        dept_id = request.form.get('dept_id')
        dept_name = request.form.get('dept_name')
        
        if not dept_id or not dept_name:
            flash("Department ID and Name are required.", "error")
            return redirect(url_for('department_management'))
        
        try:
            conn.execute("""
                INSERT INTO departments (dept_id, dept_name)
                VALUES (?, ?)
            """, (dept_id, dept_name))
            conn.commit()
            flash("Department created successfully.", "success")
        except sqlite3.IntegrityError:
            flash("Department ID already exists. Please use a unique ID.", "error")
        except Exception as e:
            flash(f"Error creating department: {e}", "error")
        return redirect(url_for('department_management'))
        
    departments = conn.execute("SELECT id, dept_id, dept_name, created_at FROM departments ORDER BY dept_name ASC").fetchall()
    conn.close()
    return render_template('department_management.html', departments=departments)

@app.route('/department/edit', methods=['POST'])
@login_required
def edit_department():
    conn = get_db_connection()
    dept_id = request.form.get('dept_id')
    dept_id_new = request.form.get('dept_id_new')
    dept_name = request.form.get('dept_name')
    
    if not dept_id or not dept_id_new or not dept_name:
        flash("All fields are required.", "error")
        return redirect(url_for('department_management'))
    
    try:
        conn.execute("""
            UPDATE departments
            SET dept_id = ?, dept_name = ?
            WHERE id = ?
        """, (dept_id_new, dept_name, dept_id))
        conn.commit()
        flash("Department updated successfully.", "success")
    except sqlite3.IntegrityError:
        flash("Department ID already exists. Please use a unique ID.", "error")
    except Exception as e:
        flash(f"Error updating department: {e}", "error")
    
    conn.close()
    return redirect(url_for('department_management'))

@app.route('/department/delete/<int:dept_id>', methods=['POST'])
@login_required
def delete_department(dept_id):
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM departments WHERE id = ?", (dept_id,))
        conn.commit()
        flash("Department deleted successfully.", "success")
    except Exception as e:
        flash(f"Error deleting department: {e}", "error")
    
    conn.close()
    return redirect(url_for('department_management'))

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
        # AI Extraction
        extracted = ai.extract_invoice_data(file_path)

        # Print Gemini Output (Temporary Debug)
        print("========== GEMINI OUTPUT ==========")
        print(extracted.model_dump())
        print("===================================")

        result = extracted.model_dump()

        # Database Connection
        conn = get_db_connection()

        supplier = conn.execute("""
            SELECT id, supplier_name
            FROM suppliers
            WHERE gst_number = ?
        """, (extracted.vendor_gst,)).fetchone()

        conn.close()

        if supplier:
            result["supplier_id"] = supplier["id"]
            result["supplier_name"] = supplier["supplier_name"]
        else:
            result["supplier_id"] = ""
            result["supplier_name"] = ""

        # Always return the result
        return result

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
            # Ensure grn.status column exists and set initial status to 'Pending QC'
            try:
                grn_cols = [c['name'] for c in cursor.execute("PRAGMA table_info(grn);").fetchall()]
                if 'status' not in grn_cols:
                    cursor.execute("ALTER TABLE grn ADD COLUMN status TEXT DEFAULT 'Pending QC'")
                cursor.execute("UPDATE grn SET status = ? WHERE id = ?", ('Pending QC', grn_id))
            except Exception:
                # non-fatal: continue without blocking the save
                pass
            
            line_items = data.get('line_items', [])
            generated_barcodes = []
            for index, item in enumerate(line_items, start=1):
                desc = item.get('description', 'Generic Item')
                qty = parse_decimal(item.get('qty'), 0.0)
                price = parse_decimal(item.get('unit_price'), 0.0)
                amount = parse_decimal(item.get('amount'), 0.0)
                
                # Check / Resolve matching internal item codes
                # Match by item_code first, then by normalized item_name to avoid duplicates
                cursor.execute("SELECT id FROM products WHERE LOWER(item_code) = ? LIMIT 1", (desc.strip().lower(),))
                prod = cursor.fetchone()
                if not prod:
                    normalized = normalize_item_text(desc)
                    cursor.execute("""
                        SELECT id
                        FROM products
                        WHERE LOWER(item_name) = ?
                           OR LOWER(item_code) = ?
                        ORDER BY
                            CASE WHEN status = 'Active' THEN 0 ELSE 1 END,
                            COALESCE(current_stock, 0) DESC,
                            id
                        LIMIT 1
                    """, (normalized, normalized))
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
                
                # GRN specific additions (stock will be posted later on explicit confirmation)
                cursor.execute("""
                    INSERT INTO grn_items (grn_id, product_id, quantity, unit, unit_price)
                    VALUES (?, ?, ?, 'Nos', ?)
                """, (grn_id, product_id, qty, price))
                grn_item_id = cursor.lastrowid
                
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
                
            # Ensure grn_items.qc_status column exists and default pending for this GRN
            try:
                gi_cols = [c['name'] for c in cursor.execute("PRAGMA table_info(grn_items);").fetchall()]
                if 'qc_status' not in gi_cols:
                    cursor.execute("ALTER TABLE grn_items ADD COLUMN qc_status TEXT DEFAULT 'Pending'")
                cursor.execute("UPDATE grn_items SET qc_status = 'Pending' WHERE grn_id = ?", (grn_id,))
            except Exception:
                pass

        return {"status": "success", "invoice_id": invoice_id, "grn_id": grn_id, "barcodes": generated_barcodes}, 200
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
        
        product_ids = request.form.getlist('product_id[]')
        qtys = request.form.getlist('qty[]')
        
        if not product_ids or not qtys or len(product_ids) != len(qtys):
            flash("Please add at least one item to proceed with dispatch.", "error")
            return redirect(url_for('item_issue'))
            
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO item_issues (issue_slip_no, issue_date, issued_to, work_order_no, issued_by)
                    VALUES (?, ?, ?, ?, ?)
                """, (slip_no, issue_date, issued_to, work_order, session.get('user_id')))
                issue_id = cursor.lastrowid
                
                for p_id_str, qty_str in zip(product_ids, qtys):
                    if not p_id_str or not qty_str:
                        continue
                    product_id = int(p_id_str)
                    qty = float(qty_str)
                    if qty <= 0:
                        raise ValueError("Quantity issued must be greater than zero.")
                    
                    product_row = cursor.execute("SELECT unit FROM products WHERE id = ?", (product_id,)).fetchone()
                    unit = product_row['unit'] if product_row else 'Nos'
                    
                    cursor.execute("""
                        INSERT INTO item_issue_items (issue_id, product_id, quantity, unit)
                        VALUES (?, ?, ?, ?)
                    """, (issue_id, product_id, qty, unit))
                    issue_item_id = cursor.lastrowid
                    
                    # Decrement Stock via helper (negative qty)
                    log_stock_movement(cursor, product_id, "ISSUE", "item_issue_items", issue_item_id, -qty)
                
                conn.commit()
                flash("Stock issued successfully.", "success")
        except sqlite3.IntegrityError:
            flash("Issue Slip Number must be unique.", "error")
        except Exception as e:
            flash(f"Error executing transaction: {e}", "error")
        return redirect(url_for('item_issue'))
        
    with get_db_connection() as conn:
        products = conn.execute(
            "SELECT id, item_code, item_name, barcode, current_stock FROM products WHERE status = 'Active' AND COALESCE(current_stock, 0) > 0 ORDER BY item_code ASC"
        ).fetchall()
        departments = conn.execute("SELECT dept_id, dept_name FROM departments ORDER BY dept_name ASC").fetchall()
        
        # Determine the next issue slip number
        row = conn.execute("SELECT issue_slip_no FROM item_issues ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            next_slip_no = "SLIP-001"
        else:
            last_slip = row['issue_slip_no']
            match = re.search(r'\d+', last_slip)
            if match:
                num_str = match.group()
                num_len = len(num_str)
                next_num = int(num_str) + 1
                prefix = last_slip[:match.start()]
                suffix = last_slip[match.end():]
                next_slip_no = f"{prefix}{str(next_num).zfill(num_len)}{suffix}"
            else:
                next_slip_no = last_slip + "-1"
                
    return render_template(
        'item_issue.html', 
        products=products, 
        departments=departments,
        next_slip_no=next_slip_no,
        today_date=date.today().strftime('%Y-%m-%d')
    )

@app.route('/inventory-return', methods=['GET', 'POST'])
@login_required
def inventory_return():
    if request.method == 'POST':
        return_id = request.form.get('return_id')
        return_date = request.form.get('return_date')
        returned_by = request.form.get('returned_by')
        dept = request.form.get('department')
        reason = request.form.get('reason')
        
        product_ids = request.form.getlist('product_id[]')
        qtys = request.form.getlist('qty[]')
        conditions = request.form.getlist('condition[]')
        
        if not return_id or not return_date or not product_ids:
            flash("Missing required fields.", "error")
            return redirect(url_for('inventory_return'))
        
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO inventory_returns (return_id, return_date, returned_by, department, reason, approved_by)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (return_id, return_date, returned_by, dept, reason, session.get('user_id')))
                ret_id = cursor.lastrowid
                
                total_items = 0
                for i in range(len(product_ids)):
                    pid = product_ids[i]
                    q = float(qtys[i]) if i < len(qtys) and qtys[i] else 0
                    cond = conditions[i] if i < len(conditions) else 'Good'
                    
                    if not pid or q <= 0:
                        continue
                    
                    cursor.execute("""
                        INSERT INTO inventory_return_items (return_id, product_id, quantity, unit, condition)
                        VALUES (?, ?, ?, 'Nos', ?)
                    """, (ret_id, int(pid), q, cond))
                    ret_item_id = cursor.lastrowid
                    
                    # Returns increase stock back
                    log_stock_movement(cursor, int(pid), "RETURN", "inventory_return_items", ret_item_id, q)
                    total_items += 1
                
                conn.commit()
                flash(f"Return entry logged successfully. {total_items} item(s) returned.", "success")
        except sqlite3.IntegrityError:
            flash("Return ID already exists in database record.", "error")
        except Exception as e:
            flash(f"Error handling entry transaction: {e}", "error")
        return redirect(url_for('inventory_return'))
        
    with get_db_connection() as conn:
        products = conn.execute("SELECT id, item_code, item_name, barcode FROM products ORDER BY item_code ASC").fetchall()
        departments = conn.execute("SELECT dept_id, dept_name FROM departments ORDER BY dept_name ASC").fetchall()
    return render_template('inventory_return.html', products=products, departments=departments)

@app.route('/inventory-status')
@login_required
def inventory_status():
    search_query = request.args.get('search', '').strip()
    product = None
    ledger = []
    products = []

    conn = get_db_connection()
    try:
        products = conn.execute("""
            SELECT id, item_code, item_name, barcode
            FROM products
            ORDER BY item_code ASC, item_name ASC
        """).fetchall()

        if search_query:
            product = conn.execute("""
                SELECT * FROM products
                WHERE lower(item_code) = lower(?)
                   OR lower(barcode) = lower(?)
                   OR lower(item_name) LIKE lower(?)
                   OR lower(item_code) LIKE lower(?)
            """, (search_query, search_query, f"%{search_query}%", f"%{search_query}%")).fetchone()

            if product:
                ensure_barcode_asset_exists(product['barcode'])
                ledger = conn.execute("""
                    SELECT moved_at, movement_type, reference_table, quantity_change, balance_after
                    FROM stock_ledger
                    WHERE product_id = ?
                    ORDER BY moved_at DESC
                """, (product['id'],)).fetchall()
    finally:
        conn.close()

    return render_template('inventory_status.html', product=product, ledger=ledger, query=search_query, products=products)


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
            # Ensure we have a valid product_property_id. If missing, try to find one
            prop_id = detail.get("product_property_id")
            if not prop_id:
                # try find any property for this product
                try:
                    row = cursor.execute("SELECT id FROM product_properties WHERE product_id = ? LIMIT 1", (data.get("product_id"),)).fetchone()
                    if row:
                        prop_id = row[0]
                    else:
                        # create a generic property so NOT NULL constraint is satisfied
                        cursor.execute("INSERT INTO product_properties (product_id, property_name) VALUES (?, ?)", (data.get("product_id"), 'General'))
                        prop_id = cursor.lastrowid
                except Exception:
                    prop_id = None

                print('[DEBUG] inserting inspection_details with prop_id=', prop_id, 'inspection_id=', inspection_id, 'detail=', detail)
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
                prop_id,
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
@login_required
def qc_sheet():
    product_id = request.args.get('product_id', type=int)
    item_name = request.args.get('item_name', '').strip()
    invoice_number = request.args.get('invoice_number', '').strip()
    invoice_date = request.args.get('invoice_date', '').strip()
    qty = request.args.get('qty', '').strip()

    conn = get_db_connection()
    product = resolve_product_for_qc(conn, product_id=product_id, item_name=item_name)
    specs = []
    last_inspection_date = None
    latest_inspection_id = None
    if product:
        latest = conn.execute(
            "SELECT id, inspection_date FROM inspection_entries WHERE product_id = ? ORDER BY inspection_date DESC, id DESC LIMIT 1",
            (product["id"],)
        ).fetchone()
        latest_inspection_id = latest["id"] if latest else None
        last_inspection_date = latest["inspection_date"] if latest else None

        if latest_inspection_id:
            rows = conn.execute("""
                SELECT
                    p.id,
                    p.property_name,
                    p.min_value,
                    p.max_value,
                    p.method,
                    d.obs1,
                    d.obs2,
                    d.obs3,
                    d.obs4,
                    d.obs5,
                    d.remarks
                FROM product_properties p
                LEFT JOIN inspection_details d
                    ON d.product_property_id = p.id
                    AND d.inspection_id = ?
                WHERE p.product_id = ?
                ORDER BY p.id
            """, (latest_inspection_id, product["id"]))
        else:
            rows = conn.execute("""
                SELECT id, property_name, min_value, max_value, method
                FROM product_properties
                WHERE product_id = ?
                ORDER BY id
            """, (product["id"],))
        specs = [dict(row) for row in rows]
    conn.close()

    return render_template(
        "qc_sheet.html",
        product=product,
        item_name=item_name,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        qty=qty,
        specs=specs,
        last_inspection_date=last_inspection_date,
        latest_inspection_id=latest_inspection_id,
    )


@app.route('/api/export-qc-excel', methods=['POST'])
@login_required
def api_export_qc_excel():
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    data = request.json or {}
    item_name = (data.get('item_name') or '').strip()
    product_id = data.get('product_id')
    qty = (data.get('qty') or '').strip()
    obs1 = (data.get('obs1') or '').strip()
    obs2 = (data.get('obs2') or '').strip()
    obs3 = (data.get('obs3') or '').strip()
    obs4 = (data.get('obs4') or '').strip()
    obs5 = (data.get('obs5') or '').strip()
    remarks = (data.get('remarks') or '').strip()
    invoice_number = (data.get('invoice_number') or '').strip()
    invoice_date = (data.get('invoice_date') or '').strip()
    status = (data.get('status') or '').strip()

    # Query product code / specifications
    conn = get_db_connection()
    part_no = ""
    properties = []
    try:
        # Fetch part_no (item_code)
        if product_id:
            prod_row = conn.execute("SELECT item_code, item_name FROM products WHERE id = ?", (product_id,)).fetchone()
            if prod_row:
                part_no = prod_row['item_code']
        else:
            # fallback match by item_name
            prod_row = conn.execute("SELECT id, item_code FROM products WHERE LOWER(item_name) = LOWER(?) LIMIT 1", (item_name,)).fetchone()
            if prod_row:
                product_id = prod_row['id']
                part_no = prod_row['item_code']

        # Fetch specifications properties
        if product_id:
            rows = conn.execute("""
                SELECT property_name, min_value, max_value, method 
                FROM product_properties 
                WHERE product_id = ?
                ORDER BY id
            """, (product_id,)).fetchall()
            properties = [dict(r) for r in rows]
    except Exception as e:
        print("[ERROR] Failed to query product specifications:", e)
    finally:
        conn.close()

    # Create Workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inspection Report"
    ws.views.sheetView[0].showGridLines = True

    # Column dimensions
    column_widths = {
        'A': 6, 'B': 26, 'C': 10, 'D': 10, 'E': 16,
        'F': 7, 'G': 7, 'H': 7, 'I': 7, 'J': 7, 'K': 20
    }
    for col, width in column_widths.items():
        ws.column_dimensions[col].width = width

    # Define Styles
    thin_side = Side(style='thin', color='000000')
    medium_side = Side(style='medium', color='000000')

    thin_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    # Fonts
    font_title = Font(name='Arial', size=13, bold=True, color='000000')
    font_header_bold = Font(name='Arial', size=9, bold=True, color='000000')
    font_logo = Font(name='Arial', size=14, bold=True, italic=True, color='FFFFFF')
    font_normal = Font(name='Arial', size=9, color='000000')
    font_normal_bold = Font(name='Arial', size=9, bold=True, color='000000')
    font_sub_bold = Font(name='Arial', size=8, bold=True, color='000000')

    # Fills
    fill_logo = PatternFill(start_color='B91C1C', end_color='B91C1C', fill_type='solid') # Red / Dark styling
    fill_header = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid') # light grey
    fill_metadata = PatternFill(start_color='F8FAFC', end_color='F8FAFC', fill_type='solid')

    def style_range(ws, cell_range, font=None, fill=None, alignment=None, border=None):
        for row in ws[cell_range]:
            for cell in row:
                if font: cell.font = font
                if fill: cell.fill = fill
                if alignment: cell.alignment = alignment
                if border: cell.border = border

    # 1. QA Logo block (A1:B3 merged)
    ws.merge_cells("A1:B3")
    ws["A1"] = "Qualität"
    style_range(ws, "A1:B3", font=font_logo, fill=fill_logo, 
                alignment=Alignment(horizontal='center', vertical='center'), border=thin_border)

    # 2. Main Title (C1:I3 merged)
    ws.merge_cells("C1:I3")
    ws["C1"] = "INWARD MATERIAL INSPECTION REPORT"
    style_range(ws, "C1:I3", font=font_title, 
                alignment=Alignment(horizontal='center', vertical='center'), border=thin_border)

    # 3. Document Details (J1:K1 and J2:K3 merged)
    ws.merge_cells("J1:K1")
    ws["J1"] = "QES/QA/14"
    style_range(ws, "J1:K1", font=font_sub_bold, 
                alignment=Alignment(horizontal='center', vertical='center'), border=thin_border)

    ws.merge_cells("J2:K3")
    ws["J2"] = "02/01.03.2025"
    style_range(ws, "J2:K3", font=font_sub_bold, 
                alignment=Alignment(horizontal='center', vertical='center'), border=thin_border)

    # 4. Metadata Details (Rows 4 to 6)
    # Row 4
    ws.merge_cells("A4:E4")
    ws["A4"] = "Material Recd.as per RCIA No. :"
    style_range(ws, "A4:E4", font=font_sub_bold, fill=fill_metadata, alignment=Alignment(horizontal='left', vertical='center'), border=thin_border)

    ws.merge_cells("F4:K4")
    ws["F4"] = f"Invoice / Challan No. & Date :  {invoice_number}  /  {invoice_date}"
    style_range(ws, "F4:K4", font=font_sub_bold, fill=fill_metadata, alignment=Alignment(horizontal='left', vertical='center'), border=thin_border)

    # Row 5
    ws.merge_cells("A5:E5")
    ws["A5"] = f"Part No. :  {part_no}"
    style_range(ws, "A5:E5", font=font_sub_bold, fill=fill_metadata, alignment=Alignment(horizontal='left', vertical='center'), border=thin_border)

    ws.merge_cells("F5:K5")
    ws["F5"] = f"Qty Recd:  {qty}"
    style_range(ws, "F5:K5", font=font_sub_bold, fill=fill_metadata, alignment=Alignment(horizontal='left', vertical='center'), border=thin_border)

    # Row 6
    ws.merge_cells("A6:E6")
    ws["A6"] = f"Description:  {item_name}"
    style_range(ws, "A6:E6", font=font_sub_bold, fill=fill_metadata, alignment=Alignment(horizontal='left', vertical='center'), border=thin_border)

    ws.merge_cells("F6:K6")
    ws["F6"] = f"Sampling QTY:  {qty}       Date: {invoice_date}"
    style_range(ws, "F6:K6", font=font_sub_bold, fill=fill_metadata, alignment=Alignment(horizontal='left', vertical='center'), border=thin_border)

    # Make row heights comfortable
    for r in (1, 2, 3, 4, 5, 6):
        ws.row_dimensions[r].height = 20

    # 5. Table Headers (Row 8 & Row 9)
    ws.row_dimensions[8].height = 24
    ws.row_dimensions[9].height = 24

    # Column A: SR NO.
    ws.merge_cells("A8:A9")
    ws["A8"] = "SR\nNO."
    style_range(ws, "A8:A9", font=font_header_bold, fill=fill_header, 
                alignment=Alignment(horizontal='center', vertical='center', wrap_text=True), border=thin_border)

    # Column B-E merged: SPECIFICATION FOR CRITICAL DIMENSION
    ws.merge_cells("B8:E8")
    ws["B8"] = "SPECIFICATION FOR CRITICAL DIMENSION"
    style_range(ws, "B8:E8", font=font_header_bold, fill=fill_header, 
                alignment=Alignment(horizontal='center', vertical='center'), border=thin_border)

    ws["B9"] = "PARAMETER"
    ws["C9"] = "MIN"
    ws["D9"] = "MAX"
    ws["E9"] = "METHOD / INSTRUMENT"
    for cell_id in ("B9", "C9", "D9", "E9"):
        ws[cell_id].font = font_header_bold
        ws[cell_id].fill = fill_header
        ws[cell_id].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        ws[cell_id].border = thin_border

    # Column F-J merged: OBSERVATIONS
    ws.merge_cells("F8:J8")
    ws["F8"] = "OBSERVATIONS"
    style_range(ws, "F8:J8", font=font_header_bold, fill=fill_header, 
                alignment=Alignment(horizontal='center', vertical='center'), border=thin_border)

    ws["F9"] = "1"
    ws["G9"] = "2"
    ws["H9"] = "3"
    ws["I9"] = "4"
    ws["J9"] = "5"
    for cell_id in ("F9", "G9", "H9", "I9", "J9"):
        ws[cell_id].font = font_header_bold
        ws[cell_id].fill = fill_header
        ws[cell_id].alignment = Alignment(horizontal='center', vertical='center')
        ws[cell_id].border = thin_border

    # Column K: REMARKS
    ws.merge_cells("K8:K9")
    ws["K8"] = "REMARKS"
    style_range(ws, "K8:K9", font=font_header_bold, fill=fill_header, 
                alignment=Alignment(horizontal='center', vertical='center'), border=thin_border)

    # 6. Data Rows
    current_row = 10
    display_props = properties if properties else [{'property_name': item_name, 'min_value': '', 'max_value': '', 'method': ''}]

    for idx, prop in enumerate(display_props, 1):
        ws.row_dimensions[current_row].height = 22
        
        ws[f"A{current_row}"] = idx
        ws[f"B{current_row}"] = prop.get('property_name', '')
        ws[f"C{current_row}"] = prop.get('min_value', '')
        ws[f"D{current_row}"] = prop.get('max_value', '')
        ws[f"E{current_row}"] = prop.get('method', '')
        
        # Populate observations and remarks on the first row
        if idx == 1:
            ws[f"F{current_row}"] = obs1
            ws[f"G{current_row}"] = obs2
            ws[f"H{current_row}"] = obs3
            ws[f"I{current_row}"] = obs4
            ws[f"J{current_row}"] = obs5
            ws[f"K{current_row}"] = remarks
        else:
            ws[f"F{current_row}"] = ""
            ws[f"G{current_row}"] = ""
            ws[f"H{current_row}"] = ""
            ws[f"I{current_row}"] = ""
            ws[f"J{current_row}"] = ""
            ws[f"K{current_row}"] = ""

        # Apply standard alignments and fonts
        ws[f"A{current_row}"].alignment = Alignment(horizontal='center', vertical='center')
        ws[f"B{current_row}"].alignment = Alignment(horizontal='left', vertical='center')
        ws[f"C{current_row}"].alignment = Alignment(horizontal='center', vertical='center')
        ws[f"D{current_row}"].alignment = Alignment(horizontal='center', vertical='center')
        ws[f"E{current_row}"].alignment = Alignment(horizontal='left', vertical='center')
        
        for o_col in ("F", "G", "H", "I", "J"):
            ws[f"{o_col}{current_row}"].alignment = Alignment(horizontal='center', vertical='center')
        
        ws[f"K{current_row}"].alignment = Alignment(horizontal='left', vertical='center')

        for col_let in ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"):
            cell = ws[f"{col_let}{current_row}"]
            cell.font = font_normal
            cell.border = thin_border

        current_row += 1

    # Add 3 empty rows at the end of the table Grid to maintain structured table length
    for extra in range(3):
        ws.row_dimensions[current_row].height = 22
        ws[f"A{current_row}"] = ""
        ws[f"B{current_row}"] = ""
        ws[f"C{current_row}"] = ""
        ws[f"D{current_row}"] = ""
        ws[f"E{current_row}"] = ""
        ws[f"F{current_row}"] = ""
        ws[f"G{current_row}"] = ""
        ws[f"H{current_row}"] = ""
        ws[f"I{current_row}"] = ""
        ws[f"J{current_row}"] = ""
        ws[f"K{current_row}"] = ""

        for col_let in ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"):
            cell = ws[f"{col_let}{current_row}"]
            cell.border = thin_border
        current_row += 1

    # 7. Footers block
    current_row += 1
    # LOT ACCEPTED / REJECTED and Remarks (A(current_row):G(current_row+1) merged)
    # Row current_row
    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=7)
    normalized_status = status.strip().upper()
    display_status = "PENDING"
    if normalized_status in ("CONFIRMED", "CONFIRM", "OK"):
        display_status = "ACCEPTED"
    elif normalized_status in ("REJECTED", "REJECT", "FAIL"):
        display_status = "REJECTED"
    ws.cell(row=current_row, column=1, value=f"LOT ACCEPTED / REJECTED :  {display_status}")
    style_range(ws, f"A{current_row}:G{current_row}", font=font_normal_bold, alignment=Alignment(horizontal='left', vertical='center'), border=thin_border)
    ws.row_dimensions[current_row].height = 22

    # Row current_row + 1
    ws.merge_cells(start_row=current_row+1, start_column=1, end_row=current_row+1, end_column=7)
    ws.cell(row=current_row+1, column=1, value=f"Remark :  {remarks}")
    style_range(ws, f"A{current_row+1}:G{current_row+1}", font=font_normal, alignment=Alignment(horizontal='left', vertical='center'), border=thin_border)
    ws.row_dimensions[current_row+1].height = 22

    # INSPECTED BY (H(current_row):I(current_row+1) merged)
    ws.merge_cells(start_row=current_row, start_column=8, end_row=current_row+1, end_column=9)
    ws.cell(row=current_row, column=8, value="INSPECTED BY :")
    style_range(ws, f"H{current_row}:I{current_row+1}", font=font_normal_bold, alignment=Alignment(horizontal='left', vertical='top'), border=thin_border)

    # APPROVED BY (J(current_row):K(current_row+1) merged)
    ws.merge_cells(start_row=current_row, start_column=10, end_row=current_row+1, end_column=11)
    ws.cell(row=current_row, column=10, value="APPROVED BY :")
    style_range(ws, f"J{current_row}:K{current_row+1}", font=font_normal_bold, alignment=Alignment(horizontal='left', vertical='top'), border=thin_border)

    # Save to BytesIO Stream
    out = BytesIO()
    wb.save(out)
    out.seek(0)

    filename = f"Inward_Inspection_Report_{item_name.replace(' ', '_')}.xlsx"
    return send_file(out, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
                     as_attachment=True, download_name=filename)


@app.route('/api/save-qc', methods=['POST'])
@login_required
def api_save_qc():
    data = request.json or {}
    print('\n[DEBUG] /api/save-qc payload:', data)
    item_name = (data.get('item_name') or '').strip()
    product_id = data.get('product_id')
    invoice_number = (data.get('invoice_number') or '').strip()
    invoice_date = (data.get('invoice_date') or '').strip()
    qty = parse_decimal(data.get('qty'), 0.0)
    inspection_date = (data.get('inspection_date') or date.today().isoformat()).strip()
    details = data.get('details', [])

    if not item_name and not product_id:
        return jsonify({"status": "error", "message": "Item name or product ID is required."}), 400

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            if product_id:
                product = resolve_product_for_qc(cursor, product_id=product_id, item_name=item_name)
            else:
                product = resolve_product_for_qc(cursor, item_name=item_name)

            if not product:
                generated_code = f"AUTO-{re.sub(r'[^A-Z0-9]', '', item_name.upper())[:10]}"
                cursor.execute(
                    "INSERT INTO products (item_code, barcode, item_name, unit, current_stock) VALUES (?, ?, ?, 'Nos', 0)",
                    (generated_code, generated_code, item_name)
                )
                product_id = cursor.lastrowid
            else:
                product_id = product['id']

            cursor.execute("""
                INSERT INTO inspection_entries (product_id, inspection_date)
                VALUES (?, ?)
            """, (product_id, inspection_date))
            inspection_id = cursor.lastrowid

            for detail in details:
                # Ensure we have a valid product_property_id. If missing, try to find one for this product
                prop_id = detail.get('product_property_id')
                if not prop_id:
                    try:
                        row = cursor.execute("SELECT id FROM product_properties WHERE product_id = ? LIMIT 1", (product_id,)).fetchone()
                        if row:
                            # row can be a tuple or Row; access first column
                            prop_id = row[0] if isinstance(row, tuple) or isinstance(row, list) else row['id']
                        else:
                            cursor.execute("INSERT INTO product_properties (product_id, property_name) VALUES (?, ?)", (product_id, 'General'))
                            prop_id = cursor.lastrowid
                    except Exception:
                        prop_id = None

                print('[DEBUG] inserting inspection_details with prop_id=', prop_id, 'inspection_id=', inspection_id, 'detail=', detail)
                cursor.execute("""
                    INSERT INTO inspection_details (
                        inspection_id,
                        product_property_id,
                        obs1,
                        obs2,
                        obs3,
                        obs4,
                        obs5,
                        remarks
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    inspection_id,
                    prop_id,
                    detail.get('obs1'),
                    detail.get('obs2'),
                    detail.get('obs3'),
                    detail.get('obs4'),
                    detail.get('obs5'),
                    detail.get('remarks'),
                ))

            # If a qc_status is provided and invoice_number is known, attempt to update matching grn_items
            qc_status = (data.get('qc_status') or '').strip()
            invoice_number = (data.get('invoice_number') or '').strip()
            if qc_status and invoice_number:
                try:
                    # map simple status values to canonical ones
                    s_norm = qc_status.strip().lower()
                    if s_norm in ('confirmed', 'confirm', 'ok'):
                        s_val = 'Confirmed'
                    elif s_norm in ('rejected', 'reject', 'fail'):
                        s_val = 'Rejected'
                    else:
                        s_val = qc_status

                    inv = cursor.execute("SELECT id FROM invoices WHERE invoice_number = ?", (invoice_number,)).fetchone()
                    if inv:
                        grn_row = cursor.execute("SELECT id FROM grn WHERE invoice_id = ? ORDER BY id DESC LIMIT 1", (inv['id'],)).fetchone()
                        if grn_row:
                            grn_id = grn_row['id']
                            # Ensure qc_status column exists
                            gi_cols = [c['name'] for c in cursor.execute("PRAGMA table_info(grn_items);").fetchall()]
                            if 'qc_status' not in gi_cols:
                                cursor.execute("ALTER TABLE grn_items ADD COLUMN qc_status TEXT DEFAULT 'Pending'")

                            if product_id:
                                cursor.execute("UPDATE grn_items SET qc_status = ? WHERE grn_id = ? AND product_id = ?", (s_val, grn_id, product_id))
                            else:
                                # try match by item_name through products
                                cursor.execute("UPDATE grn_items SET qc_status = ? WHERE grn_id = ? AND product_id IN (SELECT id FROM products WHERE item_name = ?)", (s_val, grn_id, item_name))
                except Exception:
                    pass

            conn.commit()

        return jsonify({
            "status": "success",
            "message": "QC inspection saved successfully.",
            "inspection_id": inspection_id,
            "product_id": product_id,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/apply-qc-map', methods=['POST'])
@login_required
def api_apply_qc_map():
    data = request.json or {}
    grn_id = data.get('grn_id')
    qc_map = data.get('qc_map') or {}
    if not grn_id:
        return jsonify({"status":"error","message":"grn_id required"}), 400
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # ensure qc_status column
            gi_cols = [c['name'] for c in cursor.execute("PRAGMA table_info(grn_items);").fetchall()]
            if 'qc_status' not in gi_cols:
                cursor.execute("ALTER TABLE grn_items ADD COLUMN qc_status TEXT DEFAULT 'Pending'")

            applied = 0
            for key, qc in (qc_map.items() if isinstance(qc_map, dict) else []):
                # key format invoice|description — try extract description
                try:
                    parts = key.split('|', 1)
                    description = parts[1] if len(parts) > 1 else None
                except Exception:
                    description = None
                status = qc.get('status') if isinstance(qc, dict) else None
                if not status:
                    continue
                # Normalize
                s_norm = str(status).strip().lower()
                if s_norm in ('confirmed','confirm','ok'):
                    s_val = 'Confirmed'
                elif s_norm in ('rejected','reject','fail'):
                    s_val = 'Rejected'
                else:
                    s_val = status

                if description:
                    # try exact match first
                    cursor.execute(
                        "UPDATE grn_items SET qc_status = ? WHERE grn_id = ? AND product_id IN (SELECT id FROM products WHERE LOWER(item_name) = LOWER(?))",
                        (s_val, grn_id, description)
                    )
                    applied += cursor.rowcount
                    if cursor.rowcount == 0:
                        # fallback to LIKE partial match
                        cursor.execute(
                            "UPDATE grn_items SET qc_status = ? WHERE grn_id = ? AND product_id IN (SELECT id FROM products WHERE LOWER(item_name) LIKE LOWER('%' || ? || '%'))",
                            (s_val, grn_id, description)
                        )
                        applied += cursor.rowcount

            conn.commit()
        return jsonify({"status":"success","applied": applied})
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}), 500


@app.route('/api/qc-sheet/<int:product_id>')
@login_required
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
    
# ===========================
# BARCODE SEARCH API
# ===========================

@app.route('/api/search_barcode', methods=['POST'])
def api_search_barcode():
    data = request.get_json() or {}
    barcode_no = data.get('barcode_no', '').strip()

    if not barcode_no:
        return jsonify({'success': False, 'message': 'No barcode provided'}), 400

    conn = get_db_connection()
    try:
        result = conn.execute("""
            SELECT * FROM products
            WHERE barcode = ? OR item_code = ?
        """, (barcode_no, barcode_no)).fetchone()

        if result:
            return jsonify({'success': True, 'product': dict(result)})
        else:
            return jsonify({'success': False, 'message': 'Product not found in database.'})
    finally:
        conn.close()

@app.route('/api/ai_scan_barcode', methods=['POST'])
def api_ai_scan_barcode():
    if 'image' not in request.files:
        return jsonify({'success': False, 'message': 'No image file uploaded.'}), 400

    image_file = request.files['image']
    if image_file.filename == '':
        return jsonify({'success': False, 'message': 'No selected image file.'}), 400

    # Save to a temporary location
    temp_path = os.path.join(app.root_path, 'static', 'temp_barcode.jpg')
    image_file.save(temp_path)

    extracted_code = ""
    try:
        # Pass the image to the AI logic in gemini_extractor
        result_schema = ai.extract_barcode_data(temp_path)
        extracted_code = result_schema.barcode_text.strip()
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return jsonify({'success': False, 'message': f'AI extraction failed: {str(e)}'}), 500

    # Clean up file
    if os.path.exists(temp_path):
        os.remove(temp_path)

    if not extracted_code:
        return jsonify({'success': False, 'message': 'AI could not find a barcode in the image.'}), 404

    # Now look up the extracted barcode in SQLite
    conn = get_db_connection()
    try:
        product = conn.execute("""
            SELECT * FROM products
            WHERE barcode = ? OR item_code = ?
        """, (extracted_code, extracted_code)).fetchone()

        if product:
            return jsonify({
                'success': True,
                'barcode': extracted_code,
                'product': dict(product)
            })
        else:
            return jsonify({
                'success': False,
                'barcode': extracted_code,
                'message': f'AI extracted "{extracted_code}" but it is not in the database.'
            })
    finally:
        conn.close()

# ===========================
# BARCODE DEMO SEARCH
# ===========================

@app.route('/search_barcode', methods=['GET', 'POST'])
def search_barcode():
    result = None

    if request.method == 'POST':
        barcode_no = request.form.get('barcode_no', '').strip()
        conn = get_db_connection()
        try:
            result = conn.execute("""
                SELECT *
                FROM products
                WHERE barcode = ?
                OR item_code = ?
            """, (barcode_no, barcode_no)).fetchone()

            if result:
                result = dict(result)
        finally:
            conn.close()

    return render_template(
        'barcode_search.html',
        result=result
    )

@app.route('/scanner_demo')
def scanner_demo():
    return render_template("scanner_demo.html")

@app.route('/confirm-grn/<int:grn_id>', methods=['POST'])
@login_required
def confirm_grn(grn_id):
    from datetime import datetime
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Ensure migration: add columns if missing
        cols = [c['name'] for c in cursor.execute("PRAGMA table_info(grn);").fetchall()]
        if 'status' not in cols:
            cursor.execute("ALTER TABLE grn ADD COLUMN status TEXT DEFAULT 'Pending'")
        if 'posted_by' not in cols:
            cursor.execute("ALTER TABLE grn ADD COLUMN posted_by INTEGER")
        if 'posted_date' not in cols:
            cursor.execute("ALTER TABLE grn ADD COLUMN posted_date TEXT")

        # Begin transaction explicitly
        cursor.execute('BEGIN')

        # Ensure grn_items.qc_status exists
        try:
            gi_cols = [c['name'] for c in cursor.execute("PRAGMA table_info(grn_items);").fetchall()]
            if 'qc_status' not in gi_cols:
                cursor.execute("ALTER TABLE grn_items ADD COLUMN qc_status TEXT DEFAULT 'Pending'")
        except Exception:
            pass

        grn_row = cursor.execute("SELECT * FROM grn WHERE id = ?", (grn_id,)).fetchone()
        if not grn_row:
            conn.rollback()
            return jsonify({"status": "error", "message": f"GRN {grn_id} not found."}), 404

        # SQLite Row doesn't support .get; access by key safely
        grn_status = None
        try:
            if grn_row is not None and 'status' in grn_row.keys():
                grn_status = grn_row['status']
        except Exception:
            grn_status = None

        if grn_status == 'Posted':
            conn.rollback()
            return jsonify({"status": "error", "message": "GRN has already been posted."}), 400

        items = cursor.execute("SELECT id, product_id, quantity, qc_status FROM grn_items WHERE grn_id = ?", (grn_id,)).fetchall()

        posted_any = False
        all_already_posted = True

        for item in items:
            gid = item['id']
            pid = item['product_id']
            qty = item['quantity'] or 0

            # Only process items that are QC Confirmed
            item_qc = None
            try:
                item_qc = item['qc_status']
            except Exception:
                item_qc = None

            if not item_qc or str(item_qc).strip().lower() != 'confirmed':
                # Skip items not confirmed by QC
                continue

            already = cursor.execute(
                "SELECT 1 FROM stock_ledger WHERE movement_type = 'GRN' AND reference_table = 'grn_items' AND reference_id = ? LIMIT 1",
                (gid,)
            ).fetchone()

            if already:
                # This grn_item already has a ledger entry; skip to avoid duplication
                continue
            # Use helper to update product current_stock and add ledger record
            log_stock_movement(cursor, pid, 'GRN', 'grn_items', gid, qty)
            posted_any = True
            all_already_posted = False

        # If none posted (no confirmed items), rollback and inform caller
        if not posted_any:
            conn.rollback()
            return jsonify({"status": "error", "message": "No confirmed GRN items to post."}), 400

        # Mark GRN as posted
        cursor.execute(
            "UPDATE grn SET status = ?, posted_by = ?, posted_date = ? WHERE id = ?",
            ('Posted', session.get('user_id'), datetime.utcnow().isoformat(), grn_id)
        )

        # prepare extra info to return to caller: grn_no, invoice_number, supplier_name
        try:
            info = cursor.execute("SELECT g.grn_no, inv.invoice_number, s.supplier_name FROM grn g LEFT JOIN invoices inv ON g.invoice_id = inv.id LEFT JOIN suppliers s ON g.supplier_id = s.id WHERE g.id = ?", (grn_id,)).fetchone()
            result_extra = {
                "grn_no": info['grn_no'] if info and 'grn_no' in info.keys() else None,
                "invoice_number": info['invoice_number'] if info and 'invoice_number' in info.keys() else None,
                "supplier_name": info['supplier_name'] if info and 'supplier_name' in info.keys() else None,
            }
        except Exception:
            result_extra = {"grn_no": None, "invoice_number": None, "supplier_name": None}

        conn.commit()
        resp = {"status": "success", "message": "GRN posted successfully."}
        resp.update(result_extra)
        return jsonify(resp)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass

        # =====================================================
# ADDED: FORGOT PASSWORD ROUTING HANDLERS
# =====================================================

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        # CHANGED: Read 'email' from the form submission instead of 'username'
        email = request.form.get('email', '').strip()
        
        conn = get_db_connection()
        # CHANGED: Query the database by 'email' to find the matching user profile
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        
        # Verify user exists AND check if they have a registered email
        if user and user['email']:
            username = user['username'] # Retrieve their username from the database row
            
            # Create a unique 32-character security token
            token = secrets.token_urlsafe(32)
            # Token expires 15 minutes from now (900 seconds)
            expiry = time.time() + 900 
            
            conn.execute(
                "UPDATE users SET reset_token = ?, token_expiry = ? WHERE id = ?",
                (token, expiry, user['id'])
            )
            conn.commit()
            conn.close()
            
            # Formulate outbound reset link payload
            reset_url = url_for('reset_password', token=token, _external=True)
            print("\n" + "="*60)
            print(f" PASSWORD RESET URL GENERATED FOR USER '{username}' ({email}):")
            print(f" {reset_url}")
            print("="*60 + "\n")
            
            # Trigger Live Transactive Email
            email_sent = send_recovery_email(user['email'], username, reset_url)
            
            if email_sent:
                flash(f"A password reset link has been safely dispatched to {user['email']}.", "success")
            else:
                flash("Internal transactional mail connection timeout. Token printed to local console system logs.", "success")
        else:
            if conn:
                conn.close()
            # Security best practice: keep the alert text generic so attackers don't know which emails exist
            flash("If the account exists and has a configured email profile, a link was generated. Check system console logs.", "success")
            
        return redirect(url_for('login'))
        
    return render_template('forgot_password.html')


@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    token = request.args.get('token')
    if not token:
        flash("Invalid request token syntax.", "error")
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE reset_token = ? AND token_expiry > ?", 
        (token, time.time())
    ).fetchone()
    
    if not user:
        conn.close()
        flash("The link has either expired or is invalid.", "error")
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        new_password = request.form.get('password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        
        if not new_password:
            flash("Password cannot be blank.", "error")
            conn.close()
            return render_template('reset_password.html', token=token)
            
        if new_password != confirm_password:
            flash("Passwords do not match.", "error")
            conn.close()
            return render_template('reset_password.html', token=token)
            
        # Match alignment pattern with user registration logic (plain text)
        conn.execute(
            "UPDATE users SET password_hash = ?, reset_token = NULL, token_expiry = NULL WHERE id = ?",
            (new_password, user['id'])
        )
        conn.commit()
        conn.close()
        
        flash("Your password has been successfully updated. Please log in.", "success")
        return redirect(url_for('login'))
        
    conn.close()
    return render_template('reset_password.html', token=token)


# =====================================================
# ADDED: USER ADMINISTRATIVE EMAIL DIRECTORY PANEL
# =====================================================

@app.route('/admin/users-email', methods=['GET', 'POST'])
@login_required
def manage_users_emails():
    """Administrative access dashboard to attach emails and verification flags to user accounts."""
    conn = get_db_connection()
    current_role = conn.execute("SELECT role FROM users WHERE username = ?", (session.get('user'),)).fetchone()
    
    if not current_role or current_role['role'] != 'admin':
        conn.close()
        flash('Access denied. Admin role configuration validation failed.', 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        user_id = request.form.get('user_id')
        new_email = request.form.get('email', '').strip()
        is_verified = request.form.get('verified') == '1'
        
        conn.execute(
            "UPDATE users SET email = ?, email_verified = ? WHERE id = ?", 
            (new_email if new_email else None, 1 if is_verified else 0, user_id)
        )
        conn.commit()
        flash("User profile email record updated successfully.", "success")
        
    all_users = conn.execute("SELECT id, username, role, is_active, email, email_verified FROM users ORDER BY username ASC").fetchall()
    conn.close()
    
    # CHANGED: Swapped 'user_email_management.html' to your actual file 'user_management.html'
    return render_template('user_management.html', users=all_users)


# =====================================================
# ADDED: MOBILE PHONE INVOICE SCANNING FLOW
# =====================================================

mobile_sessions = {}

def async_extract_task(file_path, session_id):
    mobile_sessions[session_id]["status"] = "processing"
    try:
        import gemini_extractor as ai
        extracted = ai.extract_invoice_data(file_path)
        result = extracted.model_dump()
        
        # Database lookup for supplier by GST
        conn = get_db_connection()
        supplier = conn.execute("""
            SELECT id, supplier_name
            FROM suppliers
            WHERE gst_number = ?
        """, (extracted.vendor_gst,)).fetchone()
        conn.close()

        if supplier:
            result["supplier_id"] = supplier["id"]
            result["supplier_name"] = supplier["supplier_name"]
        else:
            result["supplier_id"] = ""
            result["supplier_name"] = ""

        mobile_sessions[session_id]["payload"] = result
        mobile_sessions[session_id]["status"] = "success"
    except Exception as e:
        mobile_sessions[session_id]["status"] = "error"
        mobile_sessions[session_id]["error"] = str(e)
    finally:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

@app.route('/api/mobile-session/create', methods=['POST'])
def create_mobile_session():
    import uuid
    import socket
    session_id = str(uuid.uuid4())
    mobile_sessions[session_id] = {
        "status": "pending",
        "payload": None,
        "error": None
    }
    
    def get_local_ip():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('10.255.255.255', 1))
            IP = s.getsockname()[0]
        except Exception:
            IP = '127.0.0.1'
        finally:
            s.close()
        return IP
        
    local_ip = get_local_ip()
    host_parts = request.host.split(':')
    port = host_parts[1] if len(host_parts) > 1 else '5000'
    connect_url = f"http://{local_ip}:{port}/mobile-upload?session={session_id}"
    
    return jsonify({
        "session_id": session_id,
        "connect_url": connect_url
    })

@app.route('/mobile-upload', methods=['GET'])
def mobile_upload_page():
    session_id = request.args.get('session')
    if not session_id or session_id not in mobile_sessions:
        return "Invalid or expired session. Please scan a fresh QR code from your desktop.", 400
    return render_template('mobile_upload.html', session_id=session_id)

@app.route('/api/mobile-upload-submit', methods=['POST'])
def mobile_upload_submit():
    import threading
    session_id = request.form.get('session_id')
    if not session_id or session_id not in mobile_sessions:
        return jsonify({"error": "Invalid or expired session"}), 400
    
    if 'file' not in request.files:
        return jsonify({"error": "No file part in the request"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    # Save file temporarily
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"mobile_{session_id}_{file.filename}")
    file.save(file_path)
    
    # Trigger async processing thread
    thread = threading.Thread(target=async_extract_task, args=(file_path, session_id))
    thread.start()
    
    return jsonify({"status": "received", "message": "File received. Processing has started."})

@app.route('/api/mobile-status/<session_id>', methods=['GET'])
def get_mobile_status(session_id):
    status_data = mobile_sessions.get(session_id)
    if not status_data:
        return jsonify({"error": "Invalid session ID"}), 404
    return jsonify(status_data)


if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')


