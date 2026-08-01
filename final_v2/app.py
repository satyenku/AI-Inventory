# app.py

import base64
import logging
import os
import re
import sqlite3
import time
from datetime import date, datetime
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html import escape
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from init_db import init_db
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import traceback

# Configure application-level logging (replaces print() debugging)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


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

import qrcode
import gemini_extractor as ai

app = Flask(__name__)
init_db()
app.config.from_object(Config)
Config.validate()
os.makedirs(os.path.join(app.root_path, 'static', 'exports'), exist_ok=True)



# ---------------------------------------------------------------------------
# Mail settings — read exclusively from environment; never hardcoded.
# Supported .env keys:
#   MAIL_SERVER          (default: smtp.gmail.com)
#   MAIL_PORT            (default: 587)
#   MAIL_USERNAME        or EMAIL_USER   — sender login
#   MAIL_PASSWORD        or EMAIL_PASS   — sender password / app-password
#   MAIL_DEFAULT_SENDER  (default: same as MAIL_USERNAME)
# ---------------------------------------------------------------------------
_MAIL_SERVER  = os.getenv("MAIL_SERVER",  os.getenv("SMTP_SERVER",  "smtp.gmail.com"))
_MAIL_PORT    = int(os.getenv("MAIL_PORT", os.getenv("SMTP_PORT", "587")))
_MAIL_USER    = os.getenv("MAIL_USERNAME", os.getenv("EMAIL_USER", ""))
_MAIL_PASS    = os.getenv("MAIL_PASSWORD", os.getenv("EMAIL_PASS", ""))
_MAIL_SENDER  = os.getenv("MAIL_DEFAULT_SENDER", _MAIL_USER)

# Keep legacy aliases so any existing references still work
SMTP_SERVER   = _MAIL_SERVER
SMTP_PORT     = _MAIL_PORT
SMTP_USERNAME = _MAIL_USER
SMTP_PASSWORD = _MAIL_PASS

# NOTE: Credentials are read from environment — never logged to console.


def send_recovery_email(target_email: str, username: str, reset_link: str) -> bool:
    """Send an HTML password-reset email to *target_email*.

    Returns True on success, False on any failure (error is logged, not raised).
    Credentials are sourced exclusively from environment variables.
    """
    if not _MAIL_USER or not _MAIL_PASS:
        logger.error("[MAIL] Cannot send email: MAIL_USERNAME / MAIL_PASSWORD not configured in environment.")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"]    = _MAIL_SENDER
    msg["To"]      = target_email
    msg["Subject"] = "Password Reset Request — AI Inventory"

    html_body = f"""
    <div style="font-family:Arial,sans-serif;padding:28px;color:#1e293b;
                max-width:560px;border:1px solid #e5e7eb;border-radius:8px;">
        <h3 style="margin-top:0;color:#111827;">Hello {escape(username)},</h3>
        <p style="line-height:1.6;">
            We received a request to reset your <strong>AI Inventory</strong> account password.
            Click the secure button below to choose a new password.
            This link is valid for <strong>15 minutes</strong> and can only be used once.
        </p>
        <p style="margin:28px 0;">
            <a href="{reset_link}"
               style="background:#2563eb;color:#fff;padding:11px 22px;
                      text-decoration:none;border-radius:5px;
                      display:inline-block;font-weight:bold;font-size:14px;">
               Reset My Password
            </a>
        </p>
        <p style="color:#64748b;font-size:13px;line-height:1.5;">
            If the button doesn't work, copy and paste this link into your browser:<br>
            <span style="word-break:break-all;">{reset_link}</span>
        </p>
        <hr style="border:none;border-top:1px solid #f3f4f6;margin:20px 0;">
        <p style="color:#94a3b8;font-size:12px;margin:0;">
            If you did not request a password reset, you can safely ignore this email.
            Your password will not change until you click the link above.
        </p>
    </div>
    """
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(_MAIL_SERVER, _MAIL_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(_MAIL_USER, _MAIL_PASS)
            server.send_message(msg)
        logger.info("[MAIL] Password reset email sent to %s", target_email)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("[MAIL] Authentication failed — check MAIL_USERNAME / MAIL_PASSWORD.")
    except smtplib.SMTPException as exc:
        logger.error("[MAIL] SMTP error sending to %s: %s", target_email, exc)
    except Exception as exc:
        logger.error("[MAIL] Unexpected error sending to %s: %s", target_email, exc)
    return False

def login_required(f):
    """Redirect unauthenticated requests to the login page."""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Block non-admin users with a 403 page.

    Used on routes that must only be accessible to users with role='admin'.
    Staff and viewer accounts that attempt a direct URL hit receive a clear,
    styled Access Denied page — not a raw server error or a silent redirect.
    """
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            logger.warning(
                "[RBAC] User '%s' (role=%s) attempted to access admin-only route '%s'.",
                session.get('user'), session.get('role'), request.path
            )
            return render_template('403.html'), 403
        return f(*args, **kwargs)
    return decorated_function


def get_session_role():
    """Return the current session role string, lower-cased. Empty string if not set."""
    return (session.get('role') or '').lower()


def is_admin():
    """True when the logged-in user has the admin role."""
    return get_session_role() == 'admin'


def is_write_allowed():
    """True for admin and staff. False for viewer and any unrecognised role.

    Use this to gate any route or template control that creates, edits or
    deletes data.  Viewer accounts are intentionally read-only across the
    entire application except for the three pages they are allowed to see.
    """
    return get_session_role() in ('admin', 'staff')

# ── Global error handlers ────────────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404


# Ensure ledger integrity triggers and indexes exist (safe to run multiple times)
def ensure_ledger_integrity():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # unique index to prevent accidental duplicate ledger entries for same source
        # (do not include movement_type here would cause ISSUE/RETURN collisions)
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

        # Migration: add issue_item_id column to inventory_return_items if missing
        existing_cols = [row[1] for row in cur.execute("PRAGMA table_info(inventory_return_items)").fetchall()]
        if 'issue_item_id' not in existing_cols:
            cur.execute("ALTER TABLE inventory_return_items ADD COLUMN issue_item_id INTEGER REFERENCES item_issue_items(id)")

        # Migration: add email/reset_token columns to users if missing
        user_cols = [row[1] for row in cur.execute("PRAGMA table_info(users)").fetchall()]
        if 'email' not in user_cols:
            cur.execute("ALTER TABLE users ADD COLUMN email TEXT")
        if 'email_verified' not in user_cols:
            cur.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0")
        if 'reset_token' not in user_cols:
            cur.execute("ALTER TABLE users ADD COLUMN reset_token TEXT")
        if 'token_expiry' not in user_cols:
            cur.execute("ALTER TABLE users ADD COLUMN token_expiry REAL")

        # Migration: create UNIQUE index on users.email (safe — NULL values are not considered duplicates)
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON users(email) WHERE email IS NOT NULL"
        )
        # Migration: create index on reset_token for fast token lookups
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_reset_token ON users(reset_token) WHERE reset_token IS NOT NULL"
        )

        # Migration: re-hash any plain-text passwords (plain passwords don't start with known hash prefixes)
        from werkzeug.security import generate_password_hash
        plain_users = cur.execute(
            "SELECT id, password_hash FROM users WHERE password_hash NOT LIKE 'scrypt:%' AND password_hash NOT LIKE 'pbkdf2:%' AND password_hash NOT LIKE 'bcrypt:%'"
        ).fetchall()
        for pu in plain_users:
            cur.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(pu[1]), pu[0])
            )

        # Migration: add grn_item_id column to inspection_entries for production traceability
        inspection_cols = [row[1] for row in cur.execute("PRAGMA table_info(inspection_entries)").fetchall()]
        if 'grn_item_id' not in inspection_cols:
            cur.execute("ALTER TABLE inspection_entries ADD COLUMN grn_item_id INTEGER REFERENCES grn_items(id)")
            logger.info("[Migration] Added grn_item_id column to inspection_entries for GRN traceability")

        # Migration: add item_name, qc_status, invoice_number columns to export_history if missing
        export_history_cols = [row[1] for row in cur.execute("PRAGMA table_info(export_history)").fetchall()]
        if 'item_name' not in export_history_cols:
            cur.execute("ALTER TABLE export_history ADD COLUMN item_name TEXT")
        if 'qc_status' not in export_history_cols:
            cur.execute("ALTER TABLE export_history ADD COLUMN qc_status TEXT")
        if 'invoice_number' not in export_history_cols:
            cur.execute("ALTER TABLE export_history ADD COLUMN invoice_number TEXT")
            logger.info("[Migration] Added invoice_number column to export_history")

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

def generate_barcode_asset(barcode_value):
    """Generate a QR code PNG for the given value and return the file path."""
    folder = os.path.join(app.root_path, 'static', 'barcodes')
    os.makedirs(folder, exist_ok=True)
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(barcode_value)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    # Sanitize barcode_value so it is safe as a filename (no slashes, no dotdot)
    safe_stem = re.sub(r'[^\w\-.]', '_', str(barcode_value))
    safe_stem = safe_stem.replace('..', '_')
    png_path = os.path.join(folder, f"{safe_stem}.png")
    img.save(png_path)
    return png_path


def ensure_barcode_asset_exists(barcode_value):
    if not barcode_value:
        return None
    folder = os.path.join(app.root_path, 'static', 'barcodes')
    os.makedirs(folder, exist_ok=True)
    safe_stem = re.sub(r'[^\w\-.]', '_', str(barcode_value)).replace('..', '_')
    png_path = os.path.join(folder, f"{safe_stem}.png")
    if not os.path.exists(png_path):
        try:
            return generate_barcode_asset(barcode_value)
        except Exception:
            return None
    return png_path


def ensure_qr_reports_storage():
    folder = os.path.join(app.root_path, 'static', 'qr_reports')
    os.makedirs(folder, exist_ok=True)
    return folder


def resolve_qr_report_path(row):
    if not row:
        return None
    filepath = (row.get('report_filepath') or '').strip()
    if not filepath:
        return None
    if os.path.isabs(filepath):
        return filepath
    if filepath.startswith('static/'):
        return os.path.join(app.root_path, filepath)
    return os.path.join(app.root_path, 'static', 'qr_reports', row.get('report_filename') or '')


def create_qr_report_archive(payload):
    data = payload or {}
    grn_number = (data.get('grn_number') or '').strip() or 'GRN-UNASSIGNED'
    item_name = (data.get('item_name') or '').strip() or 'Unnamed Item'
    item_code = (data.get('item_code') or '').strip() or 'N/A'
    supplier_name = (data.get('supplier_name') or '').strip() or 'N/A'
    quantity = data.get('quantity') or 0
    generated_at = (data.get('generated_at') or datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')).strip()
    downloaded_at = (data.get('downloaded_at') or generated_at).strip()
    downloaded_by = (data.get('downloaded_by') or session.get('user') or 'system').strip()
    report_id = int(data.get('report_id') or (time.time() * 1000))

    folder = ensure_qr_reports_storage()
    safe_name = re.sub(r'[^A-Za-z0-9._-]+', '_', f"{grn_number}_{item_code}_{report_id}")
    filename = f"{safe_name}.html"
    filepath = os.path.join(folder, filename)

    qr_value = f"{grn_number}|{item_code}|{item_name}"
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(qr_value)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buffer = BytesIO()
    img.save(buffer, format='PNG')
    encoded_image = base64.b64encode(buffer.getvalue()).decode('ascii')

    html_content = f"""<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <title>GRN QR Report - {grn_number}</title>
    <style>
      body {{ font-family: Arial, sans-serif; padding: 24px; color: #0f172a; }}
      .card {{ border: 1px solid #cbd5e1; border-radius: 12px; padding: 24px; max-width: 700px; margin: 0 auto; }}
      .title {{ font-size: 24px; font-weight: 700; margin-bottom: 12px; }}
      .meta {{ color: #475569; margin-bottom: 10px; }}
      img {{ display: block; margin: 20px 0; }}
    </style>
  </head>
  <body>
    <div class=\"card\">
      <div class=\"title\">Confirmed GRN QR Report</div>
      <div class=\"meta\"><strong>GRN Number:</strong> {grn_number}</div>
      <div class=\"meta\"><strong>Item Name:</strong> {item_name}</div>
      <div class=\"meta\"><strong>Item Code:</strong> {item_code}</div>
      <div class=\"meta\"><strong>Supplier:</strong> {supplier_name}</div>
      <div class=\"meta\"><strong>Quantity:</strong> {quantity}</div>
      <img src=\"data:image/png;base64,{encoded_image}\" alt=\"QR Report\" />
      <div class=\"meta\"><strong>Generated At:</strong> {generated_at}</div>
    </div>
  </body>
</html>
"""

    with open(filepath, 'w', encoding='utf-8') as handle:
        handle.write(html_content)

    relative_path = os.path.relpath(filepath, app.root_path).replace('\\', '/')
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO qr_reports (
                grn_number, item_name, item_code, supplier_name, quantity,
                report_filename, report_filepath, generated_at, downloaded_at, downloaded_by, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                grn_number,
                item_name,
                item_code,
                supplier_name,
                quantity,
                filename,
                relative_path,
                generated_at,
                downloaded_at,
                downloaded_by,
                'Available',
            ),
        )
        conn.commit()
        report_row_id = cursor.lastrowid
    finally:
        conn.close()

    return {
        'id': report_row_id,
        'grn_number': grn_number,
        'item_name': item_name,
        'item_code': item_code,
        'supplier_name': supplier_name,
        'quantity': quantity,
        'report_filename': filename,
        'report_filepath': relative_path,
        'generated_at': generated_at,
        'downloaded_at': downloaded_at,
        'downloaded_by': downloaded_by,
        'status': 'Available',
    }


@app.route('/api/qr-report/download-history', methods=['GET', 'POST'])
@login_required
def api_qr_report_download_history():
    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form or {}
        try:
            record = create_qr_report_archive(data)
            return jsonify({'success': True, 'record': record})
        except Exception as exc:
            logger.error('[qr_report_history] Failed to store archive entry: %s', exc, exc_info=True)
            return jsonify({'success': False, 'message': 'Unable to save QR report archive entry.'}), 500

    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, grn_number, item_name, item_code, supplier_name, quantity,
                   report_filename, report_filepath, generated_at, downloaded_at, downloaded_by, status
            FROM qr_reports
            ORDER BY COALESCE(downloaded_at, generated_at, created_at) DESC, id DESC
            """
        ).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            resolved_path = resolve_qr_report_path(record)
            record['status'] = 'Available' if resolved_path and os.path.exists(resolved_path) else 'Missing'
            records.append(record)
    finally:
        conn.close()

    return jsonify({'success': True, 'records': records, 'total': len(records)})


@app.route('/qr-reports', methods=['GET', 'POST'])
@login_required
def qr_reports():
    if request.method == 'POST':
        data = request.form or {}
        if not (data.get('grn_number') or data.get('item_name')):
            flash('Please provide a GRN number or item name to archive a QR report.', 'error')
            return redirect(url_for('qr_reports'))
        try:
            create_qr_report_archive(data)
            flash('QR report archived successfully.', 'success')
        except Exception as exc:
            logger.error('[qr_reports] Failed to archive QR report: %s', exc, exc_info=True)
            flash('Unable to archive the QR report right now.', 'error')
        return redirect(url_for('qr_reports'))

    conn = get_db_connection()
    try:
        query = """
            SELECT id, grn_number, item_name, item_code, supplier_name, quantity,
                   report_filename, report_filepath, generated_at, downloaded_at, downloaded_by, status
            FROM qr_reports
            WHERE 1=1
        """
        params = []
        search = (request.args.get('q') or '').strip()
        if search:
            pattern = f'%{search.lower()}%'
            query += " AND (LOWER(grn_number) LIKE ? OR LOWER(item_name) LIKE ? OR LOWER(item_code) LIKE ? OR LOWER(supplier_name) LIKE ?)"
            params.extend([pattern, pattern, pattern, pattern])

        from_date = (request.args.get('from_date') or '').strip()
        if from_date:
            query += " AND COALESCE(downloaded_at, generated_at, created_at) >= ?"
            params.append(from_date)

        to_date = (request.args.get('to_date') or '').strip()
        if to_date:
            query += " AND COALESCE(downloaded_at, generated_at, created_at) <= ?"
            params.append(to_date)

        downloaded_by = (request.args.get('downloaded_by') or '').strip()
        if downloaded_by:
            query += " AND LOWER(downloaded_by) = ?"
            params.append(downloaded_by.lower())

        sort = (request.args.get('sort') or 'latest').strip().lower()
        if sort == 'oldest':
            query += " ORDER BY COALESCE(downloaded_at, generated_at, created_at) ASC, id ASC"
        elif sort == 'grn':
            query += " ORDER BY grn_number ASC, id ASC"
        else:
            query += " ORDER BY COALESCE(downloaded_at, generated_at, created_at) DESC, id DESC"

        page = max(int(request.args.get('page') or 1), 1)
        per_page = int(request.args.get('per_page') or 25)
        if per_page not in (10, 25, 50, 100):
            per_page = 25
        offset = (page - 1) * per_page
        count_row = conn.execute(f"SELECT COUNT(*) AS total FROM ({query})", params).fetchone()
        total_rows = int(count_row['total']) if count_row else 0
        query += " LIMIT ? OFFSET ?"
        params.extend([per_page, offset])
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    records = []
    for row in rows:
        record = dict(row)
        resolved_path = resolve_qr_report_path(record)
        record['status'] = 'Available' if resolved_path and os.path.exists(resolved_path) else 'Missing'
        records.append(record)

    # Group records by GRN number
    grouped_records = {}
    for record in records:
        grn = record['grn_number'] or 'N/A'
        if grn not in grouped_records:
            grouped_records[grn] = []
        grouped_records[grn].append(record)

    total_pages = max((total_rows + per_page - 1) // per_page, 1) if total_rows else 1
    return render_template(
        'qr_reports.html',
        grouped_records=grouped_records,
        total_records=total_rows,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        search=search,
        from_date=from_date,
        to_date=to_date,
        downloaded_by=downloaded_by,
        sort=sort,
        current_user=session.get('user', 'Guest'),
        current_role=session.get('role', 'viewer'),
    )


@app.route('/qr-reports/download/<int:report_id>')
@login_required
def download_qr_report(report_id):
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT id, grn_number, item_name, item_code, supplier_name, quantity,
                   report_filename, report_filepath, generated_at, downloaded_at, downloaded_by, status
            FROM qr_reports WHERE id = ?
        """, (report_id,)).fetchone()
        if not row:
            flash('QR report record not found.', 'error')
            return redirect(url_for('qr_reports'))

        record = dict(row)
        resolved_path = resolve_qr_report_path(record)
        if not resolved_path or not os.path.exists(resolved_path):
            flash('Stored QR report file is missing. Please contact an administrator.', 'error')
            return redirect(url_for('qr_reports'))

        conn.execute(
            "UPDATE qr_reports SET downloaded_at = ?, downloaded_by = ?, status = 'Available' WHERE id = ?",
            (datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), session.get('user') or 'system', report_id),
        )
        conn.commit()
    finally:
        conn.close()

    return send_file(resolved_path, as_attachment=True, download_name=record.get('report_filename') or f'qr_report_{report_id}.html')


@app.route('/qr-reports/download-grn/<grn_number>')
@login_required
def download_qr_report_by_grn(grn_number):
    conn = get_db_connection()
    try:
        # Fetch all items for this GRN with additional details
        rows = conn.execute("""
            SELECT qr.id, qr.grn_number, qr.item_name, qr.item_code, qr.supplier_name, qr.quantity,
                   qr.report_filename, qr.report_filepath, qr.generated_at, qr.downloaded_at, qr.downloaded_by, qr.status
            FROM qr_reports qr
            WHERE qr.grn_number = ?
        """, (grn_number,)).fetchall()
        
        if not rows:
            flash('No QR reports found for this GRN.', 'error')
            return redirect(url_for('qr_reports'))

        items = []
        supplier_name = ''
        for row in rows:
            record = dict(row)
            items.append(record)
            if not supplier_name and record.get('supplier_name'):
                supplier_name = record['supplier_name']

        # Fetch GRN and Invoice details for this GRN
        grn_info = conn.execute("""
            SELECT g.grn_no, g.received_date, 
                   i.invoice_number, i.invoice_date, i.vendor_name, i.total_amount,
                   s.supplier_name, s.gst_number, s.phone, s.address
            FROM grn g
            LEFT JOIN invoices i ON g.invoice_id = i.id
            LEFT JOIN suppliers s ON g.supplier_id = s.id
            WHERE g.grn_no = ?
        """, (grn_number,)).fetchone()
        
        # Use fetched info or fall back to qr_reports data
        if grn_info:
            grn_dict = dict(grn_info)
            invoice_number = grn_dict.get('invoice_number') or 'N/A'
            invoice_date = grn_dict.get('invoice_date') or 'N/A'
            vendor_name = grn_dict.get('vendor_name') or 'N/A'
            total_amount = grn_dict.get('total_amount') or 0
            supplier_name = grn_dict.get('supplier_name') or supplier_name
            supplier_gst = grn_dict.get('gst_number') or 'N/A'
            supplier_phone = grn_dict.get('phone') or 'N/A'
            supplier_address = grn_dict.get('address') or 'N/A'
            received_date = grn_dict.get('received_date') or 'N/A'
        else:
            invoice_number = 'N/A'
            invoice_date = 'N/A'
            vendor_name = 'N/A'
            total_amount = 0
            supplier_gst = 'N/A'
            supplier_phone = 'N/A'
            supplier_address = 'N/A'
            received_date = 'N/A'

        # Generate QR codes for all items
        qr_images = []
        for item in items:
            qr_value = f"{item['grn_number']}|{item['item_code']}|{item['item_name']}"
            qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
            qr.add_data(qr_value)
            qr.make(fit=True)
            img = qr.make_image(fill_color='black', back_color='white')
            buffer = BytesIO()
            img.save(buffer, format='PNG')
            encoded_image = base64.b64encode(buffer.getvalue()).decode('ascii')
            qr_images.append({
                'item_name': item['item_name'],
                'item_code': item['item_code'],
                'quantity': item['quantity'],
                'image': encoded_image
            })

        # Generate HTML content with GRN details
        items_html = ''
        for idx, qr_data in enumerate(qr_images, 1):
            items_html += f"""
            <div class="item-section">
                <div class="item-title">Item {idx}</div>
                <div class="meta"><strong>Item Name:</strong> {qr_data['item_name']}</div>
                <div class="meta"><strong>Item Code:</strong> {qr_data['item_code']}</div>
                <div class="meta"><strong>Quantity:</strong> {qr_data['quantity']}</div>
                <img src="data:image/png;base64,{qr_data['image']}" alt="QR Code" />
            </div>
            """

        html_content = f"""<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <title>Goods Receipt Note - {grn_number}</title>
    <style>
      body {{ font-family: Arial, sans-serif; padding: 25px; color: #222; margin: 0; }}
      h1 {{ text-align: center; margin-bottom: 5px; font-size: 26px; }}
      h3 {{ text-align: center; margin-top: 0; color: #555; font-weight: normal; }}
      .info-table {{ width: 100%; border-collapse: collapse; margin-top: 20px; margin-bottom: 20px; }}
      .info-table td {{ border: 1px solid #ddd; padding: 10px; font-size: 14px; }}
      .label {{ font-weight: bold; background: #f4f4f4; width: 220px; }}
      hr {{ margin: 25px 0; }}
      h2 {{ text-align: center; margin-bottom: 15px; }}
      .item-section {{ margin-top: 30px; padding: 20px; border: 1px solid #aaa; border-radius: 8px; page-break-inside: avoid; text-align: center; }}
      .item-title {{ font-size: 18px; font-weight: 600; margin-bottom: 10px; color: #1e293b; }}
      .meta {{ color: #475569; margin-bottom: 10px; }}
      img {{ display: block; margin: 20px auto; width: 180px; height: 180px; }}
      @page {{ margin: 12mm; }}
    </style>
  </head>
  <body>
    <h1>Goods Receipt Note (GRN)</h1>
    <h3>Inventory Management System</h3>
    
    <table class=\"info-table\">
      <tr>
        <td class=\"label\">Supplier</td>
        <td>{supplier_name}</td>
      </tr>
      <tr>
        <td class=\"label\">Supplier GST Number</td>
        <td>{supplier_gst}</td>
      </tr>
      <tr>
        <td class=\"label\">Supplier Phone</td>
        <td>{supplier_phone}</td>
      </tr>
      <tr>
        <td class=\"label\">Supplier Address</td>
        <td>{supplier_address}</td>
      </tr>
      <tr>
        <td class=\"label\">Invoice Number</td>
        <td>{invoice_number}</td>
      </tr>
      <tr>
        <td class=\"label\">Invoice Date</td>
        <td>{invoice_date}</td>
      </tr>
      <tr>
        <td class=\"label\">Detected Vendor</td>
        <td>{vendor_name}</td>
      </tr>
      <tr>
        <td class=\"label\">Total Invoice Value</td>
        <td>₹ {total_amount}</td>
      </tr>
      <tr>
        <td class=\"label\">GRN Number</td>
        <td><strong>{grn_number}</strong></td>
      </tr>
      <tr>
        <td class=\"label\">Received Date</td>
        <td>{received_date}</td>
      </tr>
      <tr>
        <td class=\"label\">Total Items</td>
        <td>{len(items)}</td>
      </tr>
    </table>
    
    <hr>
    
    <h2>GRN QR Labels</h2>
    
    {items_html}
    
    <div style="margin-top: 30px; text-align: center; color: #64748b; font-size: 12px;">
      Generated At: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}
    </div>
  </body>
</html>
"""

        # Update downloaded_at for all items in this GRN
        current_time = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        current_user = session.get('user') or 'system'
        conn.execute(
            "UPDATE qr_reports SET downloaded_at = ?, downloaded_by = ? WHERE grn_number = ?",
            (current_time, current_user, grn_number),
        )
        conn.commit()
    finally:
        conn.close()

    # Create temporary file
    temp_file = BytesIO()
    temp_file.write(html_content.encode('utf-8'))
    temp_file.seek(0)
    
    safe_grn = re.sub(r'[^A-Za-z0-9._-]+', '_', grn_number)
    filename = f"GRN_Report_{safe_grn}.html"
    
    return send_file(temp_file, as_attachment=True, download_name=filename, mimetype='text/html')


@app.route('/qr-reports/view/<int:report_id>')
@login_required
def view_qr_report(report_id):
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT id, report_filename, report_filepath
            FROM qr_reports WHERE id = ?
        """, (report_id,)).fetchone()
        if not row:
            flash('QR report record not found.', 'error')
            return redirect(url_for('qr_reports'))

        record = dict(row)
        resolved_path = resolve_qr_report_path(record)
        if not resolved_path or not os.path.exists(resolved_path):
            flash('Stored QR report file is missing. Please contact an administrator.', 'error')
            return redirect(url_for('qr_reports'))
    finally:
        conn.close()

    return send_file(resolved_path, mimetype='text/html', download_name=record.get('report_filename') or f'qr_report_{report_id}.html')


@app.route('/qr-reports/delete/<int:report_id>', methods=['POST'])
@login_required
def delete_qr_report(report_id):
    if not is_write_allowed():
        return render_template('403.html'), 403

    conn = get_db_connection()
    try:
        row = conn.execute("SELECT report_filename, report_filepath FROM qr_reports WHERE id = ?", (report_id,)).fetchone()
        if row:
            resolved_path = resolve_qr_report_path(dict(row))
            if resolved_path and os.path.exists(resolved_path):
                os.remove(resolved_path)
            conn.execute("DELETE FROM qr_reports WHERE id = ?", (report_id,))
            conn.commit()
            flash('QR report entry deleted successfully.', 'success')
        else:
            flash('QR report entry not found.', 'error')
    finally:
        conn.close()

    return redirect(url_for('qr_reports'))


@app.route('/generate_qr/<barcode_value>')
@login_required
def generate_qr(barcode_value):
    """Generate QR code on-the-fly and return as PNG image"""
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2
        )
        qr.add_data(barcode_value)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        
        # Save to BytesIO buffer
        buffer = BytesIO()
        img.save(buffer, 'PNG')
        buffer.seek(0)
        
        return Response(buffer.getvalue(), mimetype='image/png')
    except Exception as e:
        logger.error(f"Error generating QR code for {barcode_value}: {e}")
        # Return a 1x1 transparent PNG on error
        return Response(b'', mimetype='image/png', status=500)


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
        
        # Check password using secure hash comparison
        if user and user['is_active'] and check_password_hash(user['password_hash'], password):
            session['user'] = user['username']
            session['user_id'] = user['id']
            session['role'] = user['role']
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid credentials. Please check your username and password.", "error")
            
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
    
    # 1. Total Products in Product Master
    total_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    
    # 2. Total Suppliers
    total_suppliers = conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
    
    # 3. Total Departments
    total_departments = conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
    
    # 4. Total Inventory Items (sum of all current stock)
    total_inventory_items = conn.execute("SELECT COALESCE(SUM(current_stock), 0) FROM products").fetchone()[0]
    
    # 5. Today's GRN Count (posted GRNs)
    today_grn_count = conn.execute("""
        SELECT COUNT(*) FROM grn 
        WHERE DATE(posted_date) = DATE('now') AND status = 'Posted'
    """).fetchone()[0]
    
    # 6. Today's Issue Count
    today_issue_count = conn.execute("""
        SELECT COUNT(*) FROM item_issues 
        WHERE DATE(issue_date) = DATE('now')
    """).fetchone()[0]
    
    # 7. Today's Return Count
    today_return_count = conn.execute("""
        SELECT COUNT(*) FROM inventory_returns 
        WHERE DATE(return_date) = DATE('now')
    """).fetchone()[0]
    
    # 8. Low Stock Items (count)
    low_stock_count = conn.execute("""
        SELECT COUNT(*) FROM products 
        WHERE current_stock <= min_stock_level AND min_stock_level > 0
    """).fetchone()[0]
    
    # 9. Total Stock Value (sum of current_stock * unit_price from latest GRN)
    # Use average unit price from grn_items for each product
    total_stock_value = conn.execute("""
        SELECT COALESCE(SUM(p.current_stock * COALESCE(avg_price.avg_unit_price, 0)), 0)
        FROM products p
        LEFT JOIN (
            SELECT product_id, AVG(unit_price) as avg_unit_price
            FROM grn_items
            WHERE unit_price > 0
            GROUP BY product_id
        ) avg_price ON p.id = avg_price.product_id
    """).fetchone()[0]
    
    # 10. Current User Info
    current_user = session.get('user', 'Guest')
    current_role = session.get('role', 'viewer')
    
    # 11. Stock by Category (for pie chart)
    stock_by_category = conn.execute("""
        SELECT COALESCE(category, 'Uncategorized') as category, 
               COUNT(*) as count
        FROM products
        GROUP BY category
        ORDER BY count DESC
    """).fetchall()
    
    # 12. Daily GRN vs Issues (last 7 days) for line chart
    daily_stats = conn.execute("""
        WITH RECURSIVE dates(date) AS (
            SELECT DATE('now', '-6 days')
            UNION ALL
            SELECT DATE(date, '+1 day')
            FROM dates
            WHERE date < DATE('now')
        )
        SELECT 
            d.date,
            COALESCE(grn_count, 0) as grn_count,
            COALESCE(issue_count, 0) as issue_count
        FROM dates d
        LEFT JOIN (
            SELECT DATE(posted_date) as date, COUNT(*) as grn_count
            FROM grn
            WHERE status = 'Posted' AND DATE(posted_date) >= DATE('now', '-6 days')
            GROUP BY DATE(posted_date)
        ) g ON d.date = g.date
        LEFT JOIN (
            SELECT DATE(issue_date) as date, COUNT(*) as issue_count
            FROM item_issues
            WHERE DATE(issue_date) >= DATE('now', '-6 days')
            GROUP BY DATE(issue_date)
        ) i ON d.date = i.date
        ORDER BY d.date
    """).fetchall()
    
    # 13. Low Stock Items Details (for table)
    low_stock_items = conn.execute("""
        SELECT item_code, item_name, category, current_stock, min_stock_level, unit
        FROM products
        WHERE current_stock <= min_stock_level AND min_stock_level > 0
        ORDER BY (current_stock / NULLIF(min_stock_level, 0)) ASC
        LIMIT 10
    """).fetchall()
    
    # 14. Recent Activity (last 10 stock movements)
    recent_activity = conn.execute("""
        SELECT 
            sl.moved_at,
            p.item_code,
            p.item_name,
            sl.movement_type,
            sl.quantity_change,
            sl.balance_after,
            p.unit
        FROM stock_ledger sl
        JOIN products p ON sl.product_id = p.id
        ORDER BY sl.moved_at DESC
        LIMIT 10
    """).fetchall()
    
    conn.close()
    
    return render_template(
        'dashboard.html',
        total_products=total_products,
        total_suppliers=total_suppliers,
        total_departments=total_departments,
        total_inventory_items=total_inventory_items,
        today_grn_count=today_grn_count,
        today_issue_count=today_issue_count,
        today_return_count=today_return_count,
        low_stock_count=low_stock_count,
        total_stock_value=total_stock_value,
        current_user=current_user,
        current_role=current_role,
        stock_by_category=stock_by_category,
        daily_stats=daily_stats,
        low_stock_items=low_stock_items,
        recent_activity=recent_activity
    )

@app.route('/products', methods=['GET', 'POST'])
@login_required
def product_master():
    conn = get_db_connection()
    if request.method == 'POST':
        if not is_write_allowed():
            conn.close()
            logger.warning(
                "[RBAC] User '%s' (role=%s) attempted POST on /products.",
                session.get('user'), session.get('role')
            )
            return render_template('403.html'), 403

        item_code = request.form.get('item_code', '').strip()
        item_name = request.form.get('item_name', '').strip()
        category = request.form.get('category', '').strip()
        subcategory = request.form.get('subcategory', '').strip()
        unit = request.form.get('unit', '').strip()
        min_stock = float(request.form.get('min_stock_level') or 0)
        max_stock = float(request.form.get('max_stock_level') or 0)
        reorder_level = float(request.form.get('reorder_level') or 0)
        hsn = request.form.get('hsn_sac_code', '').strip()
        location = request.form.get('storage_location', '').strip()
        description = request.form.get('description', '').strip()

        # Validate all required fields
        if not all([item_code, item_name, category, subcategory, unit, hsn, location, description]):
            flash("All fields are mandatory except Inspection Properties.", "error")
            conn.close()
            return redirect(url_for('product_master'))

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
    can_write = is_write_allowed()
    can_delete = is_admin()  # Only admin can delete
    return render_template('product_master.html', products=products_list, can_write=can_write, can_delete=can_delete)

@app.route('/product/edit', methods=['POST'])
@login_required
def edit_product():

    if not is_write_allowed():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /product/edit.",
            session.get('user'),
            session.get('role')
        )
        return render_template("403.html"), 403

    conn = get_db_connection()
    cursor = conn.cursor()

    product_id = request.form.get("product_id")

    item_code = request.form.get("item_code", "").strip()
    item_name = request.form.get("item_name", "").strip()

    category = request.form.get("category")
    subcategory = request.form.get("subcategory", "").strip()

    unit = request.form.get("unit")

    min_stock = float(request.form.get("min_stock_level") or 0)
    max_stock = float(request.form.get("max_stock_level") or 0)
    reorder_level = float(request.form.get("reorder_level") or 0)

    hsn = request.form.get("hsn_sac_code", "").strip()
    location = request.form.get("storage_location", "").strip()
    description = request.form.get("description", "").strip()

    if not product_id or not item_code or not item_name:
        flash("Product ID, Item Code and Item Name are required.", "error")
        conn.close()
        return redirect(url_for("product_master"))

    try:
        product_id = int(product_id)

        current_product = cursor.execute("""
            SELECT id,item_code,barcode
            FROM products
            WHERE id=?
        """, (product_id,)).fetchone()

        if not current_product:
            flash("Product not found.", "error")
            conn.close()
            return redirect(url_for("product_master"))

        current_item_code = current_product["item_code"]

        if item_code != current_item_code:

            duplicate = cursor.execute("""
                SELECT id
                FROM products
                WHERE item_code=? AND id!=?
            """, (item_code, product_id)).fetchone()

            if duplicate:
                flash("SKU already exists.", "error")
                conn.close()
                return redirect(url_for("product_master"))

            duplicate_barcode = cursor.execute("""
                SELECT id
                FROM products
                WHERE barcode=? AND id!=?
            """, (item_code, product_id)).fetchone()

            if duplicate_barcode:
                flash("Barcode already exists.", "error")
                conn.close()
                return redirect(url_for("product_master"))

        cursor.execute("""
            UPDATE products
            SET
                item_code=?,
                barcode=?,
                item_name=?,
                category=?,
                subcategory=?,
                unit=?,
                min_stock_level=?,
                max_stock_level=?,
                reorder_level=?,
                hsn_sac_code=?,
                storage_location=?,
                description=?
            WHERE id=?
        """, (
            item_code,
            item_code,
            item_name,
            category,
            subcategory,
            unit,
            min_stock,
            max_stock,
            reorder_level,
            hsn,
            location,
            description,
            product_id
        ))

        # --------------------------------------------------
        # Inspection Property Lock
        # --------------------------------------------------

        qc_exists = cursor.execute("""
            SELECT 1
            FROM inspection_details id
            JOIN product_properties pp
                ON id.product_property_id = pp.id
            WHERE pp.product_id=?
            LIMIT 1
        """, (product_id,)).fetchone()

        prop_names = request.form.getlist("property_name[]")
        prop_mins = request.form.getlist("property_min[]")
        prop_maxs = request.form.getlist("property_max[]")
        prop_methods = request.form.getlist("property_method[]")

        existing_props = cursor.execute("""
            SELECT
                id,
                property_name,
                min_value,
                max_value,
                method
            FROM product_properties
            WHERE product_id=?
            ORDER BY id
        """, (product_id,)).fetchall()

        if qc_exists:

            old_props = [
                (
                    p["property_name"],
                    str(p["min_value"] or ""),
                    str(p["max_value"] or ""),
                    p["method"] or ""
                )
                for p in existing_props
            ]

            new_props = []

            for i, name in enumerate(prop_names):

                name = (name or "").strip()

                if not name:
                    continue

                new_props.append((
                    name,
                    prop_mins[i] if i < len(prop_mins) else "",
                    prop_maxs[i] if i < len(prop_maxs) else "",
                    prop_methods[i] if i < len(prop_methods) else ""
                ))

            if old_props != new_props:

                flash(
                    "Inspection properties cannot be modified because QC has already been performed. Please create a new product.",
                    "error"
                )

                conn.rollback()
                conn.close()

                return redirect(url_for("product_master"))

        else:

            properties_in_use = cursor.execute("""
                SELECT DISTINCT pp.id
                FROM product_properties pp
                INNER JOIN inspection_details id
                    ON id.product_property_id = pp.id
                WHERE pp.product_id=?
            """, (product_id,)).fetchall()

            properties_in_use_ids = [
                row["id"] for row in properties_in_use
            ] if properties_in_use else []

            existing_props_dict = {
                prop["id"]: prop
                for prop in existing_props
            }

            processed_ids = set()

            for idx, name in enumerate(prop_names):

                name = (name or "").strip()

                if not name:
                    continue

                min_val = None
                max_val = None

                try:
                    if idx < len(prop_mins) and prop_mins[idx]:
                        min_val = float(prop_mins[idx])
                except:
                    pass

                try:
                    if idx < len(prop_maxs) and prop_maxs[idx]:
                        max_val = float(prop_maxs[idx])
                except:
                    pass

                method = (
                    prop_methods[idx]
                    if idx < len(prop_methods)
                    else None
                )

                matching_prop = None

                for prop_id, prop in existing_props_dict.items():

                    if (
                        prop["property_name"] == name
                        and prop_id not in processed_ids
                    ):
                        matching_prop = prop
                        break

                if matching_prop:

                    cursor.execute("""
                        UPDATE product_properties
                        SET
                            min_value=?,
                            max_value=?,
                            method=?
                        WHERE id=?
                    """, (
                        min_val,
                        max_val,
                        method,
                        matching_prop["id"]
                    ))

                    processed_ids.add(matching_prop["id"])

                else:

                    insert_product_property(
                        cursor,
                        product_id,
                        name,
                        min_val,
                        max_val,
                        method
                    )

            for prop_id in existing_props_dict.keys():

                if prop_id not in processed_ids:

                    if prop_id not in properties_in_use_ids:

                        cursor.execute("""
                            DELETE FROM product_properties
                            WHERE id=?
                        """, (prop_id,))

                    else:

                        logger.warning(
                            "[edit_product] Cannot delete property %s because it has QC history.",
                            prop_id
                        )

        conn.commit()

        ensure_barcode_asset_exists(item_code)

        flash(
            "Product updated successfully!",
            "success"
        )

        logger.info(
            "[edit_product] Product ID %s updated by user '%s'",
            product_id,
            session.get("user")
        )

    except sqlite3.IntegrityError as e:

        import traceback
        traceback.print_exc()

        conn.rollback()

        error_msg = str(e).lower()

        if "unique" in error_msg or "item_code" in error_msg:

            flash(
                "SKU already exists. Please use a different SKU.",
                "error"
            )

        elif "barcode" in error_msg:

            flash(
                "Barcode already exists. Please use a different barcode.",
                "error"
            )

        elif "foreign key" in error_msg:

            flash(
                "Cannot update because related records exist.",
                "error"
            )

        else:

            flash(str(e), "error")

        logger.error(
            "[edit_product] IntegrityError: %s",
            str(e),
            exc_info=True
        )

    except Exception as e:

        conn.rollback()

        logger.error(
            "[edit_product] Error: %s",
            str(e),
            exc_info=True
        )

        flash(
            "An error occurred while updating the product.",
            "error"
        )

    finally:

        conn.close()

    return redirect(url_for("product_master"))

@app.route('/product/delete/<int:product_id>', methods=['POST'])
@login_required
def delete_product(product_id):
    # Only Admin can delete products
    if not is_admin():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /product/delete.",
            session.get('user'), session.get('role')
        )
        return render_template('403.html'), 403

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # ✅ Check if product exists
        product = cursor.execute("SELECT item_name, item_code FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            flash("Product not found.", "error")
            conn.close()
            return redirect(url_for('product_master'))
        
        product_name = product['item_name']
        product_code = product['item_code']
        
        # ✅ Check if product has any stock movements in ledger
        ledger_count = cursor.execute(
            "SELECT COUNT(*) as count FROM stock_ledger WHERE product_id = ?", 
            (product_id,)
        ).fetchone()['count']
        
        if ledger_count > 0:
            flash(
                f"❌ Cannot delete product '{product_name}' ({product_code}). "
                f"This product has {ledger_count} stock movement(s) recorded in the ledger. "
                f"Products with transaction history cannot be deleted for audit compliance.",
                "error"
            )
            conn.close()
            return redirect(url_for('product_master'))
        
        # ✅ Check if product is referenced in any GRN items
        grn_count = cursor.execute(
            "SELECT COUNT(*) as count FROM grn_items WHERE product_id = ?", 
            (product_id,)
        ).fetchone()['count']
        
        if grn_count > 0:
            flash(
                f"❌ Cannot delete product '{product_name}' ({product_code}). "
                f"This product is referenced in {grn_count} GRN item(s). "
                f"Products with GRN history cannot be deleted.",
                "error"
            )
            conn.close()
            return redirect(url_for('product_master'))
        
        # ✅ Check if product is referenced in any issue items
        issue_count = cursor.execute(
            "SELECT COUNT(*) as count FROM item_issue_items WHERE product_id = ?", 
            (product_id,)
        ).fetchone()['count']
        
        if issue_count > 0:
            flash(
                f"❌ Cannot delete product '{product_name}' ({product_code}). "
                f"This product is referenced in {issue_count} item issue(s). "
                f"Products with issue history cannot be deleted.",
                "error"
            )
            conn.close()
            return redirect(url_for('product_master'))
        
        # ✅ Check if product is referenced in any return items
        return_count = cursor.execute(
            "SELECT COUNT(*) as count FROM inventory_return_items WHERE product_id = ?", 
            (product_id,)
        ).fetchone()['count']
        
        if return_count > 0:
            flash(
                f"❌ Cannot delete product '{product_name}' ({product_code}). "
                f"This product is referenced in {return_count} inventory return(s). "
                f"Products with return history cannot be deleted.",
                "error"
            )
            conn.close()
            return redirect(url_for('product_master'))
        
        # ✅ Check if product has any current stock
        current_stock = cursor.execute(
            "SELECT current_stock FROM products WHERE id = ?", 
            (product_id,)
        ).fetchone()['current_stock']
        
        if current_stock != 0:
            flash(
                f"❌ Cannot delete product '{product_name}' ({product_code}). "
                f"This product has current stock balance of {current_stock}. "
                f"Only products with zero stock can be deleted.",
                "error"
            )
            conn.close()
            return redirect(url_for('product_master'))
        
        # ✅ All validations passed - safe to delete
        # Delete inspection properties first (CASCADE would handle this, but explicit is better)
        cursor.execute("DELETE FROM product_properties WHERE product_id = ?", (product_id,))
        
        # Delete inspection entries if any
        cursor.execute("DELETE FROM inspection_entries WHERE product_id = ?", (product_id,))
        
        # Delete product
        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        
        conn.commit()
        flash(f"✅ Product '{product_name}' ({product_code}) deleted successfully.", "success")
        
    except Exception as e:
        conn.rollback()
        logger.error("[delete_product] %s", e, exc_info=True)
        flash("An error occurred while deleting the product. Please try again.", "error")
    finally:
        conn.close()
    
    return redirect(url_for('product_master'))

@app.route('/api/product/<int:product_id>', methods=['GET'])
@login_required
def get_product_details(product_id):
    """API endpoint to fetch product details including inspection properties"""

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get product details
        product = cursor.execute("""
            SELECT *
            FROM products
            WHERE id = ?
        """, (product_id,)).fetchone()

        if not product:
            conn.close()
            return jsonify({"error": "Product not found"}), 404

        # Get inspection properties
        properties = cursor.execute("""
            SELECT property_name,
                   min_value,
                   max_value,
                   method
            FROM product_properties
            WHERE product_id = ?
            ORDER BY id
        """, (product_id,)).fetchall()

        # Check whether QC has already been performed
        qc_exists = cursor.execute("""
            SELECT 1
            FROM inspection_details id
            JOIN product_properties pp
                ON id.product_property_id = pp.id
            WHERE pp.product_id = ?
            LIMIT 1
        """, (product_id,)).fetchone()

        # Convert to dictionary
        product_dict = dict(product)
        product_dict["properties"] = [dict(prop) for prop in properties]
        product_dict["inspection_locked"] = bool(qc_exists)

        conn.close()

        return jsonify(product_dict)

    except Exception as e:
        logger.error("[get_product_details] Error: %s", e, exc_info=True)
        conn.close()
        return jsonify({"error": "Failed to fetch product details"}), 500

@app.route('/api/product', methods=['POST'])
@login_required
def api_add_product():
    """API endpoint to add a new product and return JSON response"""
    
    if not is_write_allowed():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /api/product.",
            session.get('user'), session.get('role')
        )
        return jsonify({"error": "Permission denied"}), 403

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        item_code = request.form.get('item_code', '').strip()
        item_name = request.form.get('item_name', '').strip()
        category = request.form.get('category', '').strip()
        subcategory = request.form.get('subcategory', '').strip()
        unit = request.form.get('unit', '').strip()
        min_stock = float(request.form.get('min_stock_level') or 0)
        max_stock = float(request.form.get('max_stock_level') or 0)
        reorder_level = float(request.form.get('reorder_level') or 0)
        hsn = request.form.get('hsn_sac_code', '').strip()
        location = request.form.get('storage_location', '').strip()
        description = request.form.get('description', '').strip()

        # Validate all required fields
        if not all([item_code, item_name, category, subcategory, unit, hsn, location, description]):
            conn.close()
            return jsonify({"error": "All fields are mandatory except Inspection Properties"}), 400

        # Check for duplicate product code or name
        normalized_name = normalize_item_text(item_name)
        existing_product = conn.execute(
            "SELECT id FROM products WHERE LOWER(item_code) = ? OR LOWER(item_name) = ? LIMIT 1",
            (item_code.strip().lower(), normalized_name)
        ).fetchone()
        
        if existing_product:
            conn.close()
            return jsonify({"error": "Product Code or Product Name already exists"}), 400

        # Insert new product
        cur = conn.execute("""
            INSERT INTO products 
            (item_code, barcode, item_name, category, subcategory, unit, min_stock_level, max_stock_level, reorder_level, hsn_sac_code, storage_location, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (item_code, item_code, item_name, category, subcategory, unit, min_stock, max_stock, reorder_level, hsn, location, description))
        
        product_id = cur.lastrowid

        # Handle inspection properties if provided
        prop_names = request.form.getlist('property_name[]')
        prop_mins = request.form.getlist('property_min[]')
        prop_maxs = request.form.getlist('property_max[]')
        prop_methods = request.form.getlist('property_method[]')

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
        
        # Generate barcode
        ensure_barcode_asset_exists(item_code)
        
        conn.close()
        
        return jsonify({
            "success": True,
            "message": "Product added successfully",
            "product_id": product_id,
            "item_name": item_name
        }), 201

    except sqlite3.IntegrityError as e:
        conn.close()
        logger.error("[api_add_product] IntegrityError: %s", e)
        return jsonify({"error": "Product Code or Barcode already exists"}), 400
    except Exception as e:
        conn.close()
        logger.error("[api_add_product] Error: %s", e, exc_info=True)
        return jsonify({"error": "Failed to add product"}), 500

@app.route('/suppliers', methods=['GET', 'POST'])
@login_required
def supplier_management():
    can_manage = is_admin()
    conn = get_db_connection()

    if request.method == 'POST':
        # Hard block — even if someone crafts a direct POST
        if not can_manage:
            conn.close()
            logger.warning(
                "[RBAC] User '%s' (role=%s) attempted POST on /suppliers.",
                session.get('user'), session.get('role')
            )
            return render_template('403.html'), 403

        name       = request.form.get('supplier_name', '').strip()
        gst_number = request.form.get('gst_number', '').strip().upper()
        contact    = request.form.get('contact_person', '').strip()
        phone      = request.form.get('phone', '').strip()
        email      = request.form.get('email', '').strip()
        address    = request.form.get('address', '').strip()

        try:
            conn.execute("""
                INSERT INTO suppliers
                (supplier_name, gst_number, contact_person, phone, email, address)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, gst_number, contact, phone, email, address))
            conn.commit()
            flash("Supplier record created successfully.", "success")

        except sqlite3.IntegrityError:
            flash("GST Number already exists.", "error")

        finally:
            conn.close()

        return redirect(url_for('supplier_management'))

    suppliers = conn.execute(
        "SELECT * FROM suppliers ORDER BY supplier_name ASC"
    ).fetchall()
    conn.close()

    return render_template(
        'supplier_management.html',
        suppliers=suppliers,
        can_manage=can_manage
    )

@app.route('/supplier/edit', methods=['POST'])
@login_required
def edit_supplier():
    if not is_admin():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /supplier/edit.",
            session.get('user'), session.get('role')
        )
        return render_template('403.html'), 403

    conn = get_db_connection()
    supplier_id = request.form.get('supplier_id')
    supplier_name = request.form.get('supplier_name')
    gst_number = request.form.get('gst_number')
    contact_person = request.form.get('contact_person')
    phone = request.form.get('phone')
    email = request.form.get('email')
    address = request.form.get('address')
    
    if not supplier_id or not supplier_name or not gst_number:
        flash("Supplier ID, Name, and GST Number are required.", "error")
        conn.close()
        return redirect(url_for('supplier_management'))
    
    try:
        conn.execute("""
            UPDATE suppliers
            SET supplier_name = ?, gst_number = ?, contact_person = ?, 
                phone = ?, email = ?, address = ?
            WHERE id = ?
        """, (supplier_name, gst_number.strip().upper(), contact_person, 
              phone, email, address, supplier_id))
        conn.commit()
        flash("Supplier updated successfully.", "success")
    except sqlite3.IntegrityError:
        flash("GST Number already exists. Please use a unique GST Number.", "error")
    except Exception as e:
        logger.error("[edit_supplier] %s", e, exc_info=True)
        flash("An error occurred while updating the supplier. Please try again.", "error")
    
    conn.close()
    return redirect(url_for('supplier_management'))

@app.route('/supplier/delete/<int:supplier_id>', methods=['POST'])
@login_required
def delete_supplier(supplier_id):
    if not is_admin():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /supplier/delete.",
            session.get('user'), session.get('role')
        )
        return render_template('403.html'), 403

    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))
        conn.commit()
        flash("Supplier deleted successfully.", "success")
    except Exception as e:
        logger.error("[delete_supplier] %s", e, exc_info=True)
        flash("An error occurred while deleting the supplier. Please try again.", "error")
    
    conn.close()
    return redirect(url_for('supplier_management'))

@app.route('/departments', methods=['GET', 'POST'])
@login_required
def department_management():
    can_manage = is_admin()
    conn = get_db_connection()

    if request.method == 'POST':
        if not can_manage:
            conn.close()
            logger.warning(
                "[RBAC] User '%s' (role=%s) attempted POST on /departments.",
                session.get('user'), session.get('role')
            )
            return render_template('403.html'), 403

        dept_id   = request.form.get('dept_id')
        dept_name = request.form.get('dept_name')

        if not dept_id or not dept_name:
            flash("Department ID and Name are required.", "error")
            conn.close()
            return redirect(url_for('department_management'))

        try:
            conn.execute(
                "INSERT INTO departments (dept_id, dept_name) VALUES (?, ?)",
                (dept_id, dept_name)
            )
            conn.commit()
            flash("Department created successfully.", "success")
        except sqlite3.IntegrityError:
            flash("Department ID already exists. Please use a unique ID.", "error")
        except Exception as e:
            logger.error("[department_management create] %s", e, exc_info=True)
            flash("An error occurred while creating the department. Please try again.", "error")

        conn.close()
        return redirect(url_for('department_management'))

    departments = conn.execute(
        "SELECT id, dept_id, dept_name, created_at FROM departments ORDER BY dept_name ASC"
    ).fetchall()
    conn.close()
    return render_template('department_management.html', departments=departments, can_manage=can_manage)

@app.route('/department/edit', methods=['POST'])
@login_required
def edit_department():
    if not is_admin():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /department/edit.",
            session.get('user'), session.get('role')
        )
        return render_template('403.html'), 403

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
        logger.error("[edit_department] %s", e, exc_info=True)
        flash("An error occurred while updating the department. Please try again.", "error")
    
    conn.close()
    return redirect(url_for('department_management'))

@app.route('/department/delete/<int:dept_id>', methods=['POST'])
@login_required
def delete_department(dept_id):
    if not is_admin():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /department/delete.",
            session.get('user'), session.get('role')
        )
        return render_template('403.html'), 403

    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM departments WHERE id = ?", (dept_id,))
        conn.commit()
        flash("Department deleted successfully.", "success")
    except Exception as e:
        logger.error("[delete_department] %s", e, exc_info=True)
        flash("An error occurred while deleting the department. Please try again.", "error")
    
    conn.close()
    return redirect(url_for('department_management'))

@app.route('/item-entry')
@login_required
def item_entry():
    # Viewer cannot access item entry
    if not is_write_allowed():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted access to /item-entry.",
            session.get('user'), session.get('role')
        )
        return render_template('403.html'), 403
    
    conn = get_db_connection()
    suppliers = conn.execute("SELECT * FROM suppliers").fetchall()
    conn.close()
    return render_template('item_entry.html', suppliers=suppliers)

@app.route('/api/extract', methods=['POST'])
@login_required
def api_extract_data():
    # Viewer cannot extract invoices
    if not is_write_allowed():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /api/extract.",
            session.get('user'), session.get('role')
        )
        return {"error": "Access denied"}, 403
    
    if 'file' not in request.files:
        return {"error": "Missing upload file payload"}, 400

    file = request.files['file']
    if not file or file.filename == '':
        return {"error": "No file selected"}, 400

    # Validate file extension — only PDF allowed for invoice extraction
    original_name = file.filename or ''
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in ('.pdf',):
        return {"error": "Only PDF files are accepted for invoice extraction."}, 400

    # Use a safe server-generated filename to prevent path traversal
    safe_name = f"invoice_upload_{secrets.token_hex(8)}.pdf"
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
    file.save(file_path)

    try:
        # ✅ STEP 1: AI Extraction - ALWAYS extract invoice data first
        logger.info("[api_extract_data] Starting AI extraction from file: %s", safe_name)
        extracted = ai.extract_invoice_data(file_path)
        result = extracted.model_dump()
        logger.info("[api_extract_data] AI extraction completed. Invoice: %s, Items: %d", 
                   extracted.invoice_number, len(extracted.line_items))

        # ✅ STEP 2: Search Supplier Master (non-blocking lookup only)
        conn = get_db_connection()
        supplier = None
        
        # Try match by GST first (preferred method)
        if extracted.vendor_gst:
            logger.info("[api_extract_data] Searching supplier by GST: %s", extracted.vendor_gst)
            supplier = conn.execute("""
                SELECT id, supplier_name, gst_number
                FROM suppliers
                WHERE UPPER(TRIM(gst_number)) = UPPER(TRIM(?))
            """, (extracted.vendor_gst,)).fetchone()
        
        # If no GST match, try by supplier name (fallback)
        if not supplier and extracted.vendor_name:
            logger.info("[api_extract_data] No GST match, trying by name: %s", extracted.vendor_name)
            supplier = conn.execute("""
                SELECT id, supplier_name, gst_number
                FROM suppliers
                WHERE LOWER(TRIM(supplier_name)) = LOWER(TRIM(?))
            """, (extracted.vendor_name,)).fetchone()
        
        conn.close()

        # ✅ STEP 3: Build response with ALL extracted data + supplier match status
        # CRITICAL: Always include line_items regardless of supplier match
        if supplier:
            result["supplier_id"] = supplier["id"]
            result["supplier_name"] = supplier["supplier_name"]
            result["supplier_matched"] = True
            result["supplier_match_method"] = "gst" if extracted.vendor_gst else "name"
            result["supplier_not_found"] = False
            logger.info("[api_extract_data] ✅ Supplier matched: %s (ID: %s)", 
                       supplier["supplier_name"], supplier["id"])
        else:
            result["supplier_id"] = None
            result["supplier_name"] = None
            result["supplier_matched"] = False
            result["supplier_not_found"] = True
            result["extracted_gst"] = extracted.vendor_gst or ""
            result["extracted_vendor_name"] = extracted.vendor_name or ""
            logger.warning("[api_extract_data] ❌ Supplier NOT found - GST: %s, Name: %s. " 
                         "Returning all extracted data anyway.",
                         extracted.vendor_gst, extracted.vendor_name)

        # ✅ STEP 4: Return complete extraction result
        # This MUST include: invoice_number, invoice_date, vendor_name, vendor_gst, 
        # line_items[], total_amount, and supplier matching flags
        logger.info("[api_extract_data] Returning response with %d line items (supplier_matched=%s)", 
                   len(result.get('line_items', [])), result.get('supplier_matched', False))
        return result

    except Exception as e:
        logger.error("[api_extract_data] Extraction failed: %s", e, exc_info=True)
        return {"error": "Invoice extraction failed. Please try again or enter details manually."}, 500

    finally:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info("[api_extract_data] Cleaned up temp file: %s", safe_name)
            except Exception as cleanup_err:
                logger.warning("[api_extract_data] Failed to cleanup temp file: %s", cleanup_err)

@app.route('/api/save', methods=['POST'])

@login_required

def api_save_invoice():
    # Viewer cannot save invoices
    if not is_write_allowed():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /api/save.",
            session.get('user'), session.get('role')
        )
        return {"error": "Access denied"}, 403



    data = request.json



    try:



        with get_db_connection() as conn:



            cursor = conn.cursor()



            # -------------------------------

            # Validate Supplier

            # -------------------------------



            supplier_id_raw = data.get('supplier_id')



            if not supplier_id_raw:

                return {

                    "error": "Supplier is required. Please select a supplier."

                }, 400



            try:

                supplier_id = int(supplier_id_raw)

            except (ValueError, TypeError):

                return {

                    "error": f"Invalid supplier ID : {supplier_id_raw}"

                }, 400



            cursor.execute(

                "SELECT id,supplier_name FROM suppliers WHERE id=?",

                (supplier_id,)

            )



            supplier_row = cursor.fetchone()



            if not supplier_row:

                return {

                    "error": "Supplier not found."

                }, 400



            # -------------------------------

            # Create Invoice

            # -------------------------------



            vendor_name = data.get("vendor_name", "Unknown")



            invoice_num = data.get("invoice_number", "").strip()



            if not invoice_num:

                invoice_num = f"UNMAPPED-{int(time.time())}"



            total_amt = parse_decimal(

                data.get("total_amount"),

                0.0

            )



            cursor.execute("""

                INSERT INTO invoices

                (

                    vendor_name,

                    invoice_number,

                    invoice_date,

                    supplier_id,

                    total_amount

                )

                VALUES

                (

                    ?,?,?,?,?

                )

            """,

            (

                vendor_name,

                invoice_num,

                data.get("invoice_date", ""),

                supplier_id,

                total_amt

            ))



            invoice_id = cursor.lastrowid



            # -------------------------------

            # Create GRN

            # -------------------------------



            grn_no = f"GRN-{invoice_num}-{secrets.token_hex(4).upper()}"



            received_by_id = session.get("user_id")



            if not received_by_id:

                return {

                    "error": "Invalid session."

                }, 401



            received_by_id = int(received_by_id)



            cursor.execute("""

                INSERT INTO grn

                (

                    grn_no,

                    invoice_id,

                    supplier_id,

                    received_date,

                    received_by

                )

                VALUES

                (

                    ?,?,?,?,?

                )

            """,

            (

                grn_no,

                invoice_id,

                supplier_id,

                data.get("invoice_date", ""),

                received_by_id

            ))



            grn_id = cursor.lastrowid



            try:



                cols = [

                    c["name"]

                    for c in cursor.execute(

                        "PRAGMA table_info(grn)"

                    ).fetchall()

                ]



                if "status" not in cols:



                    cursor.execute("""

                    ALTER TABLE grn

                    ADD COLUMN status TEXT

                    DEFAULT 'Pending QC'

                    """)



                cursor.execute("""

                    UPDATE grn

                    SET status='Pending QC'

                    WHERE id=?

                """,(grn_id,))



            except:

                pass



            # -------------------------------

            # Line Items

            # -------------------------------



            line_items = data.get("line_items", [])



            generated_barcodes = []



            for index, item in enumerate(line_items, start=1):



                desc = item.get("description","Generic Item")



                qty = parse_decimal(

                    item.get("qty"),

                    0

                )



                price = parse_decimal(

                    item.get("unit_price"),

                    0

                )



                amount = parse_decimal(

                    item.get("amount"),

                    0

                )



                # =====================================

                # PRODUCT LOOKUP (FIXED)

                # =====================================



                normalized = normalize_item_text(desc)



                cursor.execute("""

                    SELECT id

                    FROM products

                    WHERE LOWER(item_name)=?

                    LIMIT 1

                """,(normalized,))



                prod = cursor.fetchone()



                if not prod:



                    item_code = (

                        "AUTO-"

                        +

                        re.sub(

                            r'[^A-Z0-9]',

                            '',

                            desc.upper()

                        )[:10]

                    )



                    cursor.execute("""

                        SELECT id

                        FROM products

                        WHERE item_code=?

                        LIMIT 1

                    """,(item_code,))



                    prod = cursor.fetchone()



                if prod:



                    product_id = prod["id"]



                else:



                    item_code = (

                        "AUTO-"

                        +

                        re.sub(

                            r'[^A-Z0-9]',

                            '',

                            desc.upper()

                        )[:10]

                    )



                    cursor.execute("""

                        INSERT INTO products

                        (

                            item_code,

                            barcode,

                            item_name,

                            unit,

                            current_stock

                        )

                        VALUES

                        (

                            ?,?,?, 'Nos',0

                        )

                    """,

                    (

                        item_code,

                        item_code,

                        desc

                    ))



                    product_id = cursor.lastrowid



                    ensure_barcode_asset_exists(item_code)

                                    # -------------------------------
                # Add Invoice Item
                # -------------------------------

                cursor.execute("""
                    INSERT INTO invoice_items
                    (
                        invoice_id,
                        product_id,
                        item_name,
                        quantity,
                        unit,
                        unit_price,
                        line_total
                    )
                    VALUES
                    (
                        ?, ?, ?, ?, 'Nos', ?, ?
                    )
                """,
                (
                    invoice_id,
                    product_id,
                    desc,
                    qty,
                    price,
                    amount
                ))

                invoice_item_id = cursor.lastrowid


                # -------------------------------
                # Add GRN Item
                # -------------------------------

                cursor.execute("""
                    INSERT INTO grn_items
                    (
                        grn_id,
                        product_id,
                        quantity,
                        unit,
                        unit_price
                    )
                    VALUES
                    (
                        ?, ?, ?, 'Nos', ?
                    )
                """,
                (
                    grn_id,
                    product_id,
                    qty,
                    price
                ))

                grn_item_id = cursor.lastrowid


                # -------------------------------
                # Generate Unique Barcode
                # -------------------------------

                unique_suffix = str(
                    int(time.time() * 1000) % 100000 + index
                )

                barcode_no = (
                    f"{invoice_num}-{index}-{unique_suffix}"
                )

                barcode_file_path = generate_barcode_asset(
                    barcode_no
                )

                barcode_path = os.path.relpath(
                    barcode_file_path,
                    app.root_path
                )


                cursor.execute("""
                    INSERT INTO barcode_registry
                    (
                        barcode_no,
                        invoice_id,
                        invoice_item_id,
                        barcode_image
                    )
                    VALUES
                    (
                        ?, ?, ?, ?
                    )
                """,
                (
                    barcode_no,
                    invoice_id,
                    invoice_item_id,
                    barcode_path
                ))


                generated_barcodes.append({

                    "barcode_no": barcode_no,

                    "item_name": desc,

                    "barcode_image":
                        "/" +
                        barcode_path.replace("\\", "/")

                })


            # -------------------------------
            # QC Status
            # -------------------------------

            try:

                cols = [

                    c["name"]

                    for c in cursor.execute(

                        "PRAGMA table_info(grn_items)"

                    ).fetchall()

                ]

                if "qc_status" not in cols:

                    cursor.execute("""

                        ALTER TABLE grn_items

                        ADD COLUMN qc_status TEXT

                        DEFAULT 'Pending'

                    """)

                cursor.execute("""

                    UPDATE grn_items

                    SET qc_status='Pending'

                    WHERE grn_id=?

                """,

                (grn_id,))

            except:

                pass


            conn.commit()


        return {

            "status": "success",

            "invoice_id": invoice_id,

            "grn_id": grn_id,

            "barcodes": generated_barcodes

        }, 200


    except Exception as e:

        import traceback

        logger.error("[api_save_invoice] Unhandled error: %s", traceback.format_exc())

        return {

            "error": "An internal error occurred while saving the GRN. Please try again."

        }, 500

# ============================================================
# PRODUCT RECIPE / BOM ROUTES
# ============================================================

@app.route('/product-recipe', methods=['GET', 'POST'])
@login_required
def product_recipe():
    """Product Recipe / Bill of Materials (BOM) page"""
    if not is_write_allowed():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted access to /product-recipe.",
            session.get('user'), session.get('role')
        )
        return render_template('403.html'), 403
    
    conn = get_db_connection()
    
    if request.method == 'POST':
        recipe_id = request.form.get('recipe_id', '').strip()
        finished_product_id = request.form.get('finished_product_id')
        recipe_name = request.form.get('recipe_name', '').strip()
        description = request.form.get('description', '').strip()
        
        raw_material_ids = request.form.getlist('raw_material_id[]')
        quantity_requireds = request.form.getlist('quantity_required[]')
        units = request.form.getlist('unit[]')
        
        if not finished_product_id:
            flash("Please select a finished product.", "error")
            conn.close()
            return redirect(url_for('product_recipe'))
        
        if not raw_material_ids or len(raw_material_ids) == 0:
            flash("Please add at least one raw material.", "error")
            conn.close()
            return redirect(url_for('product_recipe'))
        
        try:
            cursor = conn.cursor()
            
            if recipe_id:
                # Update existing recipe
                cursor.execute("""
                    UPDATE bom_recipes 
                    SET finished_product_id=?, recipe_name=?, description=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (finished_product_id, recipe_name, description, recipe_id))
                
                # Delete old materials
                cursor.execute("DELETE FROM bom_recipe_items WHERE recipe_id=?", (recipe_id,))
                r_id = int(recipe_id)
                flash("Recipe updated successfully!", "success")
            else:
                # Check if recipe already exists for this product
                existing = cursor.execute(
                    "SELECT id FROM bom_recipes WHERE finished_product_id=?",
                    (finished_product_id,)
                ).fetchone()
                
                if existing:
                    flash("A recipe already exists for this finished product. Please edit the existing recipe.", "error")
                    conn.close()
                    return redirect(url_for('product_recipe'))
                
                # Create new recipe
                cursor.execute("""
                    INSERT INTO bom_recipes (finished_product_id, recipe_name, description, created_by)
                    VALUES (?, ?, ?, ?)
                """, (finished_product_id, recipe_name, description, session.get('user_id')))
                r_id = cursor.lastrowid
                flash("Recipe created successfully!", "success")
            
            # Insert materials
            for i, raw_mat_id in enumerate(raw_material_ids):
                if not raw_mat_id or i >= len(quantity_requireds) or i >= len(units):
                    continue
                
                qty = float(quantity_requireds[i])
                unit = units[i]
                
                if qty <= 0:
                    continue
                
                cursor.execute("""
                    INSERT INTO bom_recipe_items (recipe_id, raw_material_id, quantity_required, unit)
                    VALUES (?, ?, ?, ?)
                """, (r_id, raw_mat_id, qty, unit))
            
            conn.commit()
            
        except Exception as e:
            logger.error("[product_recipe] Error: %s", e, exc_info=True)
            flash(f"An error occurred: {str(e)}", "error")
        
        conn.close()
        return redirect(url_for('product_recipe'))
    
    # GET request - display page
    cursor = conn.cursor()
    
    # Get all products for dropdown
    products = cursor.execute("SELECT * FROM products ORDER BY item_code ASC").fetchall()
    
    # Get all recipes with materials
    recipes_raw = cursor.execute("""
        SELECT 
            br.id,
            br.finished_product_id,
            br.recipe_name,
            br.description,
            p.item_code as finished_product_code,
            p.item_name as finished_product_name
        FROM bom_recipes br
        JOIN products p ON br.finished_product_id = p.id
        ORDER BY p.item_code ASC
    """).fetchall()
    
    recipes = []
    for recipe in recipes_raw:
        materials = cursor.execute("""
            SELECT 
                bri.raw_material_id,
                bri.quantity_required,
                bri.unit,
                p.item_code,
                p.item_name
            FROM bom_recipe_items bri
            JOIN products p ON bri.raw_material_id = p.id
            WHERE bri.recipe_id = ?
            ORDER BY p.item_code ASC
        """, (recipe['id'],)).fetchall()
        
        recipes.append({
            'id': recipe['id'],
            'finished_product_id': recipe['finished_product_id'],
            'finished_product_code': recipe['finished_product_code'],
            'finished_product_name': recipe['finished_product_name'],
            'recipe_name': recipe['recipe_name'],
            'description': recipe['description'],
            'materials': materials
        })
    
    conn.close()
    
    return render_template('product_recipe.html', products=products, recipes=recipes)


@app.route('/product-recipe/get/<int:recipe_id>', methods=['GET'])
@login_required
def get_recipe(recipe_id):
    """Get recipe details as JSON for editing"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    recipe = cursor.execute("""
        SELECT id, finished_product_id, recipe_name, description
        FROM bom_recipes
        WHERE id = ?
    """, (recipe_id,)).fetchone()
    
    if not recipe:
        conn.close()
        return jsonify({'error': 'Recipe not found'}), 404
    
    materials = cursor.execute("""
        SELECT raw_material_id, quantity_required, unit
        FROM bom_recipe_items
        WHERE recipe_id = ?
    """, (recipe_id,)).fetchall()
    
    conn.close()
    
    return jsonify({
        'id': recipe['id'],
        'finished_product_id': recipe['finished_product_id'],
        'recipe_name': recipe['recipe_name'],
        'description': recipe['description'],
        'materials': [dict(m) for m in materials]
    })


@app.route('/product-recipe/delete/<int:recipe_id>', methods=['POST'])
@login_required
def delete_recipe(recipe_id):
    """Delete a recipe"""
    if not is_write_allowed():
        return jsonify({'error': 'Permission denied'}), 403
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if recipe exists
        recipe = cursor.execute("SELECT id FROM bom_recipes WHERE id=?", (recipe_id,)).fetchone()
        if not recipe:
            conn.close()
            return jsonify({'error': 'Recipe not found'}), 404
        
        # Delete recipe (cascade will delete items)
        cursor.execute("DELETE FROM bom_recipes WHERE id=?", (recipe_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error("[delete_recipe] Error: %s", e, exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/recipe/calculate', methods=['POST'])
@login_required
def calculate_recipe_materials():
    """Calculate required materials based on recipe and quantity"""
    data = request.json
    finished_product_id = data.get('finished_product_id')
    quantity = data.get('quantity', 1)
    
    if not finished_product_id:
        return jsonify({'error': 'Product ID required'}), 400
    
    try:
        quantity = float(quantity)
        if quantity <= 0:
            return jsonify({'error': 'Quantity must be positive'}), 400
    except:
        return jsonify({'error': 'Invalid quantity'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Find recipe
    recipe = cursor.execute("""
        SELECT id FROM bom_recipes WHERE finished_product_id=?
    """, (finished_product_id,)).fetchone()
    
    if not recipe:
        conn.close()
        return jsonify({'error': 'No recipe found for this product'}), 404
    
    # Get materials
    materials = cursor.execute("""
        SELECT 
            bri.raw_material_id,
            bri.quantity_required,
            bri.unit,
            p.item_code,
            p.item_name,
            p.current_stock
        FROM bom_recipe_items bri
        JOIN products p ON bri.raw_material_id = p.id
        WHERE bri.recipe_id = ?
    """, (recipe['id'],)).fetchall()
    
    conn.close()
    
    calculated_materials = []
    for material in materials:
        calculated_qty = material['quantity_required'] * quantity
        calculated_materials.append({
            'product_id': material['raw_material_id'],
            'item_code': material['item_code'],
            'item_name': material['item_name'],
            'quantity': calculated_qty,
            'unit': material['unit'],
            'current_stock': material['current_stock']
        })
    
    return jsonify({
        'success': True,
        'materials': calculated_materials
    })


@app.route('/item-issue', methods=['GET', 'POST'])
@login_required
def item_issue():
    # Viewer cannot access item issue
    if not is_write_allowed():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted access to /item-issue.",
            session.get('user'), session.get('role')
        )
        return render_template('403.html'), 403
    
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
            logger.error("[item_issue] Error: %s", e, exc_info=True)
            flash("An error occurred while processing the dispatch. Please try again.", "error")
        return redirect(url_for('item_issue'))
        
    with get_db_connection() as conn:
        # Get only FINISHED products that have recipes defined
        finished_products = conn.execute("""
            SELECT DISTINCT p.id, p.item_code, p.item_name, p.barcode, p.current_stock 
            FROM products p
            INNER JOIN bom_recipes br ON p.id = br.finished_product_id
            WHERE p.status = 'Active'
            ORDER BY p.item_code ASC
        """).fetchall()
        
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
        products=finished_products, 
        departments=departments,
        next_slip_no=next_slip_no,
        today_date=date.today().strftime('%Y-%m-%d')
    )

@app.route('/api/issue-items/<int:issue_id>')
@login_required
def api_issue_items(issue_id):
    """Return issued items for a given issue slip, including barcode, already-returned qty, and supplier info."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT
                iii.id          AS issue_item_id,
                iii.product_id,
                iii.quantity    AS issued_qty,
                iii.unit,
                p.item_code,
                p.item_name,
                p.barcode,
                COALESCE(
                    (SELECT SUM(r.quantity)
                     FROM inventory_return_items r
                     WHERE r.issue_item_id = iii.id),
                0) AS already_returned
            FROM item_issue_items iii
            JOIN products p ON iii.product_id = p.id
            WHERE iii.issue_id = ?
            ORDER BY p.item_code ASC
        """, (issue_id,)).fetchall()

        items = []
        for row in rows:
            issued    = float(row['issued_qty'] or 0)
            returned  = float(row['already_returned'] or 0)
            remaining = round(issued - returned, 6)

            # Fetch supplier info via the most recent GRN that received this product
            sup = conn.execute("""
                SELECT
                    s.supplier_name,
                    s.contact_person,
                    s.phone,
                    s.email,
                    s.address,
                    inv.invoice_number,
                    inv.invoice_date
                FROM grn_items gi
                JOIN grn        g   ON gi.grn_id     = g.id
                JOIN invoices   inv ON g.invoice_id  = inv.id
                LEFT JOIN suppliers s ON inv.supplier_id = s.id
                WHERE gi.product_id = ?
                ORDER BY g.id DESC
                LIMIT 1
            """, (row['product_id'],)).fetchone()

            items.append({
                'issue_item_id':    row['issue_item_id'],
                'product_id':       row['product_id'],
                'item_code':        row['item_code'],
                'item_name':        row['item_name'],
                'barcode':          row['barcode'] or '',
                'unit':             row['unit'] or 'Nos',
                'issued_qty':       issued,
                'already_returned': returned,
                'remaining_qty':    remaining,
                'supplier_name':    sup['supplier_name']  if sup else '',
                'contact_person':   sup['contact_person'] if sup else '',
                'phone':            sup['phone']          if sup else '',
                'email':            sup['email']          if sup else '',
                'address':          sup['address']        if sup else '',
                'invoice_number':   sup['invoice_number'] if sup else '',
                'invoice_date':     sup['invoice_date']   if sup else '',
            })
    finally:
        conn.close()

    return jsonify({'items': items})


@app.route('/inventory-return', methods=['GET', 'POST'])
@login_required
def inventory_return():
    # Viewer cannot access inventory return
    if not is_write_allowed():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted access to /inventory-return.",
            session.get('user'), session.get('role')
        )
        return render_template('403.html'), 403
    
    if request.method == 'POST':
        return_id   = request.form.get('return_id')
        return_date = request.form.get('return_date')
        returned_by = request.form.get('returned_by')
        dept        = request.form.get('department')
        reason      = request.form.get('reason')
        issue_id    = request.form.get('issue_id')

        issue_item_ids = request.form.getlist('issue_item_id[]')
        product_ids    = request.form.getlist('product_id[]')
        qtys           = request.form.getlist('qty[]')
        conditions     = request.form.getlist('condition[]')

        if not return_id or not return_date or not issue_id or not issue_item_ids:
            flash("Missing required fields.", "error")
            return redirect(url_for('inventory_return'))

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()

                # ── Backend validation: check remaining qty for every row ──
                for i, iii_id_str in enumerate(issue_item_ids):
                    if not iii_id_str:
                        continue
                    iii_id = int(iii_id_str)
                    q = float(qtys[i]) if i < len(qtys) and qtys[i] else 0
                    if q <= 0:
                        continue

                    row = cursor.execute("""
                        SELECT iii.quantity AS issued_qty,
                               COALESCE(
                                   (SELECT SUM(r.quantity)
                                    FROM inventory_return_items r
                                    WHERE r.issue_item_id = ?),
                               0) AS already_returned
                        FROM item_issue_items iii
                        WHERE iii.id = ?
                    """, (iii_id, iii_id)).fetchone()

                    if not row:
                        raise ValueError(f"Issue item ID {iii_id} not found.")

                    issued    = float(row['issued_qty'] or 0)
                    returned  = float(row['already_returned'] or 0)
                    remaining = issued - returned

                    if q > remaining:
                        prod_row = cursor.execute(
                            "SELECT item_name FROM products WHERE id = ?",
                            (int(product_ids[i]),)
                        ).fetchone()
                        pname = prod_row['item_name'] if prod_row else f"product_id={product_ids[i]}"
                        raise ValueError(
                            f"Return qty {q} exceeds remaining qty {remaining} for '{pname}'."
                        )

                # ── Insert return header ──
                cursor.execute("""
                    INSERT INTO inventory_returns
                        (return_id, return_date, returned_by, department, reason, approved_by)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (return_id, return_date, returned_by, dept, reason, session.get('user_id')))
                ret_id = cursor.lastrowid

                # ── Insert return items and update stock ──
                total_items = 0
                for i, iii_id_str in enumerate(issue_item_ids):
                    if not iii_id_str:
                        continue
                    iii_id = int(iii_id_str)
                    pid    = int(product_ids[i]) if i < len(product_ids) and product_ids[i] else None
                    q      = float(qtys[i]) if i < len(qtys) and qtys[i] else 0
                    cond   = conditions[i] if i < len(conditions) else 'Good'

                    if not pid or q <= 0:
                        continue

                    unit_row = cursor.execute(
                        "SELECT unit FROM item_issue_items WHERE id = ?", (iii_id,)
                    ).fetchone()
                    unit = unit_row['unit'] if unit_row else 'Nos'

                    cursor.execute("""
                        INSERT INTO inventory_return_items
                            (return_id, product_id, quantity, unit, condition, issue_item_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (ret_id, pid, q, unit, cond, iii_id))
                    ret_item_id = cursor.lastrowid

                    # Only add to stock if condition is NOT damaged
                    if cond.lower() != 'damaged':
                        log_stock_movement(cursor, pid, "RETURN", "inventory_return_items", ret_item_id, q)
                    
                    total_items += 1

                conn.commit()
                flash(f"Return entry logged successfully. {total_items} item(s) returned.", "success")

        except sqlite3.IntegrityError:
            flash("Return ID already exists in database record.", "error")
        except ValueError as ve:
            flash(str(ve), "error")
        except Exception as e:
            logger.error("[inventory_return] Error: %s", e, exc_info=True)
            flash("An error occurred while processing the return. Please try again.", "error")

        return redirect(url_for('inventory_return'))

    # ── GET ──
    with get_db_connection() as conn:
        issues = conn.execute("""
            SELECT id, issue_slip_no, issue_date, issued_to
            FROM item_issues
            ORDER BY id DESC
        """).fetchall()
        departments = conn.execute(
            "SELECT dept_id, dept_name FROM departments ORDER BY dept_name ASC"
        ).fetchall()

    return render_template('inventory_return.html', issues=issues, departments=departments)

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

    can_write = is_write_allowed()
    return render_template('inventory_status.html', product=product, ledger=ledger, query=search_query, products=products, can_write=can_write)


@app.route('/users', methods=['GET', 'POST'])
@login_required
def user_management():
    """User management — admin only. Staff/viewer receive HTTP 403."""
    if not is_admin():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted to access /users.",
            session.get('user'), session.get('role')
        )
        return render_template('403.html'), 403

    if request.method == 'POST':
        action = request.form.get('action')
        conn = get_db_connection()

        if action == 'add':
            username  = request.form.get('username', '').strip()
            password  = request.form.get('password', '').strip()
            full_name = request.form.get('full_name', '').strip()
            role      = request.form.get('role', 'staff')
            email     = request.form.get('email', '').strip().lower() or None

            if not username or not password:
                flash('Username and password are required.', 'error')
                conn.close()
                return redirect(url_for('user_management'))

            if len(password) < 8:
                flash('Password must be at least 8 characters long.', 'error')
                conn.close()
                return redirect(url_for('user_management'))

            # Validate email format when provided
            _email_re = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
            if email and not _email_re.match(email):
                flash('Please enter a valid email address.', 'error')
                conn.close()
                return redirect(url_for('user_management'))

            try:
                conn.execute(
                    "INSERT INTO users (username, password_hash, full_name, role, email) VALUES (?,?,?,?,?)",
                    (username, generate_password_hash(password), full_name, role, email)
                )
                conn.commit()
                flash(f"User '{username}' created successfully.", 'success')
            except sqlite3.IntegrityError as exc:
                err = str(exc).lower()
                if 'email' in err:
                    flash('That email address is already registered to another account.', 'error')
                else:
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
            if not new_pass:
                flash('New password cannot be blank.', 'error')
            elif len(new_pass) < 8:
                flash('Password must be at least 8 characters long.', 'error')
            else:
                conn.execute(
                    "UPDATE users SET password_hash=? WHERE id=?",
                    (generate_password_hash(new_pass), uid)
                )
                conn.commit()
                flash('Password updated successfully.', 'success')

        elif action == 'edit_email':
            uid   = request.form.get('user_id')
            email = request.form.get('email', '').strip().lower() or None
            _email_re = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
            if email and not _email_re.match(email):
                flash('Please enter a valid email address.', 'error')
            else:
                try:
                    conn.execute(
                        "UPDATE users SET email = ? WHERE id = ?",
                        (email, uid)
                    )
                    conn.commit()
                    flash('Email address updated.', 'success')
                except sqlite3.IntegrityError:
                    flash('That email address is already registered to another account.', 'error')

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
    return render_template('user_management.html', users=users, can_manage=True)


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
    # Viewer cannot save inspection
    if not is_write_allowed():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /api/save-inspection.",
            session.get('user'), session.get('role')
        )
        return jsonify({"error": "Access denied"}), 403
    

    data = request.json

    try:

        conn = get_db_connection()
        cursor = conn.cursor()

        product_id = data.get("product_id")
        grn_item_id = data.get("grn_item_id")  # NEW: capture grn_item_id
        inspection_date = data.get("inspection_date")

        # NEW: If grn_item_id not provided but product_id is, try to find latest grn_item
        if not grn_item_id and product_id:
            try:
                gi_row = cursor.execute(
                    "SELECT id FROM grn_items WHERE product_id = ? ORDER BY id DESC LIMIT 1",
                    (product_id,)
                ).fetchone()
                if gi_row:
                    grn_item_id = gi_row[0] if isinstance(gi_row, (tuple, list)) else gi_row['id']
                    logger.info("[save_inspection] Auto-resolved grn_item_id=%s for product_id=%s", grn_item_id, product_id)
            except Exception as e:
                logger.warning("[save_inspection] Could not auto-resolve grn_item_id: %s", e)

        cursor.execute("""
            INSERT INTO inspection_entries
            (
                product_id,
                grn_item_id,
                inspection_date
            )
            VALUES (?, ?, ?)
        """, (
            product_id,
            grn_item_id,
            inspection_date
        ))

        inspection_id = cursor.lastrowid

        details = data.get("details", [])

        for detail in details:
            # Ensure we have a valid product_property_id. If missing, try to find one
            prop_id = detail.get("product_property_id")
            if not prop_id:
                # try find any property for this product
                try:
                    row = cursor.execute("SELECT id FROM product_properties WHERE product_id = ? LIMIT 1", (product_id,)).fetchone()
                    if row:
                        prop_id = row[0]
                    else:
                        # create a generic property so NOT NULL constraint is satisfied
                        cursor.execute("INSERT INTO product_properties (product_id, property_name) VALUES (?, ?)", (product_id, 'General'))
                        prop_id = cursor.lastrowid
                except Exception:
                    prop_id = None

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
            "message": "Inspection saved successfully.",
            "inspection_id": inspection_id,
            "grn_item_id": grn_item_id  # NEW: return for reference
        })

    except Exception as e:
        logger.error("[save_inspection] Error: %s", e, exc_info=True)
        return jsonify({
            "status": "error",
            "message": "Inspection save failed. Please try again."
        }), 500

@app.route('/qc-sheet')
@login_required
def qc_sheet():
    product_id = request.args.get('product_id', type=int)
    item_name = request.args.get('item_name', '').strip()
    invoice_number = request.args.get('invoice_number', '').strip()
    invoice_date = request.args.get('invoice_date', '').strip()
    qty = request.args.get('qty', '').strip()
    grn_item_id = request.args.get('grn_item_id', type=int)  # NEW: accept grn_item_id parameter

    conn = get_db_connection()
    product = resolve_product_for_qc(conn, product_id=product_id, item_name=item_name)
    specs = []
    last_inspection_date = None
    latest_inspection_id = None
    latest_grn_item_id = None  # NEW: track grn_item_id from latest inspection
    
    if product:
        latest = conn.execute(
            "SELECT id, inspection_date, grn_item_id FROM inspection_entries WHERE product_id = ? ORDER BY inspection_date DESC, id DESC LIMIT 1",
            (product["id"],)
        ).fetchone()
        latest_inspection_id = latest["id"] if latest else None
        last_inspection_date = latest["inspection_date"] if latest else None
        latest_grn_item_id = latest["grn_item_id"] if latest else None  # NEW: get grn_item_id

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

    # NEW: If grn_item_id not provided but invoice_number is, try to resolve it
    if not grn_item_id and invoice_number and product:
        try:
            inv = conn.execute("SELECT id FROM invoices WHERE invoice_number = ?", (invoice_number,)).fetchone()
            if inv:
                grn_row = conn.execute("SELECT id FROM grn WHERE invoice_id = ? ORDER BY id DESC LIMIT 1", (inv['id'],)).fetchone()
                if grn_row:
                    gi_row = conn.execute("SELECT id FROM grn_items WHERE grn_id = ? AND product_id = ? ORDER BY id DESC LIMIT 1", (grn_row['id'], product["id"])).fetchone()
                    if gi_row:
                        grn_item_id = gi_row['id']
        except Exception:
            pass

    conn.close()

    return render_template(
        "qc_sheet.html",
        product=product,
        item_name=item_name,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        qty=qty,
        grn_item_id=grn_item_id or latest_grn_item_id,  # NEW: pass grn_item_id to template
        specs=specs,
        last_inspection_date=last_inspection_date,
        latest_inspection_id=latest_inspection_id,
    )


@app.route('/api/export-qc-excel', methods=['POST'])
@login_required
def api_export_qc_excel():
    # Viewer cannot export QC excel
    if not is_write_allowed():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /api/export-qc-excel.",
            session.get('user'), session.get('role')
        )
        return jsonify({"error": "Access denied"}), 403
    
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
    details = data.get('details', [])
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
                SELECT id, property_name, min_value, max_value, method 
                FROM product_properties 
                WHERE product_id = ?
                ORDER BY id
            """, (product_id,)).fetchall()
            properties = [dict(r) for r in rows]
    except Exception as e:
        logger.error("[api_export_qc_excel] Failed to query product specifications: %s", e)
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

    # Convert details array to lookup map
    details_map = {}
    for det in details:
        p_id = det.get('product_property_id')
        p_name = (det.get('property_name') or '').strip().lower()
        if p_id:
            details_map[str(p_id)] = det
        if p_name:
            details_map[p_name] = det

    for idx, prop in enumerate(display_props, 1):
        ws.row_dimensions[current_row].height = 22
        
        ws[f"A{current_row}"] = idx
        ws[f"B{current_row}"] = prop.get('property_name', '')
        ws[f"C{current_row}"] = prop.get('min_value', '')
        ws[f"D{current_row}"] = prop.get('max_value', '')
        ws[f"E{current_row}"] = prop.get('method', '')
        
        # Match observations in details_map
        prop_id_str = str(prop.get('id') or '')
        prop_name_str = (prop.get('property_name') or '').strip().lower()
        
        det_obs = details_map.get(prop_id_str) or details_map.get(prop_name_str)
        if det_obs:
            ws[f"F{current_row}"] = (det_obs.get('obs1') or '').strip()
            ws[f"G{current_row}"] = (det_obs.get('obs2') or '').strip()
            ws[f"H{current_row}"] = (det_obs.get('obs3') or '').strip()
            ws[f"I{current_row}"] = (det_obs.get('obs4') or '').strip()
            ws[f"J{current_row}"] = (det_obs.get('obs5') or '').strip()
            ws[f"K{current_row}"] = (det_obs.get('remarks') or '').strip()
        else:
            # Fallback to global values on the first row
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

    # Generate a unique filename
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    clean_item_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', item_name)
    filename = f"Item_Report_{clean_item_name}_{timestamp}.xlsx"

    # Save permanently on the server (do not delete after download)
    filepath = os.path.join(app.root_path, 'static', 'exports', filename)
    wb.save(filepath)
    file_size = os.path.getsize(filepath)

    # Create a record in the database
    total_items = parse_decimal(qty, fallback=0.0)
    created_by = session.get('user', 'system')
    export_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if status != 'rejected':
        conn = get_db_connection()
        try:
            conn.execute("""
                INSERT INTO export_history (file_name, item_name, qc_status, invoice_number, export_time, total_items, file_size, file_path, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (filename, item_name, status, invoice_number, export_time, total_items, file_size, f"static/exports/{filename}", created_by))
            conn.commit()
        except Exception as dbe:
            logger.error(f"[api_export_qc_excel] Failed to log export history: {dbe}")
        finally:
            conn.close()

    # Serve the saved file for user's download
    return send_file(filepath, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
                     as_attachment=True, download_name=filename)


@app.route('/export-history', methods=['GET'])
@login_required
def export_history():
    conn = get_db_connection()
    try:
        # Sort by export_time in descending order (latest first)
        exports = conn.execute("""
            SELECT id, file_name, item_name, qc_status, invoice_number, export_time, total_items, file_size, file_path, created_by 
            FROM export_history 
            ORDER BY export_time DESC
        """).fetchall()
        
        # Group exports by invoice_number
        grouped = {}
        invoice_order = []
        for x in exports:
            item = dict(x)
            inv = item.get('invoice_number') or ''
            if inv not in grouped:
                grouped[inv] = []
                invoice_order.append(inv)
            grouped[inv].append(item)
            
        grouped_exports = [(inv, grouped[inv]) for inv in invoice_order]
    except Exception as e:
        logger.error(f"[export_history] Failed to fetch export history: {e}")
        grouped_exports = []
    finally:
        conn.close()
        
    return render_template(
        'export_history.html', 
        grouped_exports=grouped_exports, 
        current_user=session.get('user', 'Guest'), 
        current_role=session.get('role', 'viewer')
    )


@app.route('/export-history/download/<int:export_id>', methods=['GET'])
@login_required
def download_export(export_id):
    conn = get_db_connection()
    export_row = None
    try:
        export_row = conn.execute("""
            SELECT file_name, file_path 
            FROM export_history 
            WHERE id = ?
        """, (export_id,)).fetchone()
    except Exception as e:
        logger.error(f"[download_export] DB fetch failed for ID {export_id}: {e}")
    finally:
        conn.close()

    if not export_row:
        return "Export record not found.", 404

    # Resolve safe file path relative to either app.root_path or direct file_path
    relative_path = export_row['file_path']
    # If path starts with static/exports, prefix with root_path
    if relative_path.startswith('static/'):
        filepath = os.path.join(app.root_path, relative_path)
    else:
        filepath = os.path.join(app.root_path, 'static', 'exports', export_row['file_name'])

    if not os.path.exists(filepath):
        return "Exported spreadsheet file not found on the server filesystem.", 404

    return send_file(
        filepath, 
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
        as_attachment=True, 
        download_name=export_row['file_name']
    )


@app.route('/api/save-qc', methods=['POST'])
@login_required
def api_save_qc():
    # Viewer cannot save QC
    if not is_write_allowed():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /api/save-qc.",
            session.get('user'), session.get('role')
        )
        return jsonify({"error": "Access denied"}), 403
    
    data = request.json or {}
    item_name = (data.get('item_name') or '').strip()
    product_id = data.get('product_id')
    grn_item_id = data.get('grn_item_id')  # NEW: capture grn_item_id from request
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

            # NEW: If grn_item_id not provided, try to find it from invoice_number + product_id
            if not grn_item_id and invoice_number and product_id:
                try:
                    inv = cursor.execute("SELECT id FROM invoices WHERE invoice_number = ?", (invoice_number,)).fetchone()
                    if inv:
                        grn_row = cursor.execute("SELECT id FROM grn WHERE invoice_id = ? ORDER BY id DESC LIMIT 1", (inv['id'],)).fetchone()
                        if grn_row:
                            grn_id = grn_row['id']
                            gi_row = cursor.execute("SELECT id FROM grn_items WHERE grn_id = ? AND product_id = ? ORDER BY id DESC LIMIT 1", (grn_id, product_id)).fetchone()
                            if gi_row:
                                grn_item_id = gi_row['id']
                                logger.info("[api_save_qc] Auto-resolved grn_item_id=%s for product_id=%s, invoice=%s", grn_item_id, product_id, invoice_number)
                except Exception as e:
                    logger.warning("[api_save_qc] Could not auto-resolve grn_item_id: %s", e)

            # NEW: Insert inspection with grn_item_id for full traceability
            cursor.execute("""
                INSERT INTO inspection_entries (product_id, grn_item_id, inspection_date)
                VALUES (?, ?, ?)
            """, (product_id, grn_item_id, inspection_date))
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

                            if grn_item_id:
                                # Update specific grn_item by ID (most precise)
                                cursor.execute("UPDATE grn_items SET qc_status = ? WHERE id = ?", (s_val, grn_item_id))
                            elif product_id:
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
            "grn_item_id": grn_item_id,  # NEW: return grn_item_id for reference
        })
    except Exception as e:
        logger.error("[api_save_qc] Error: %s", traceback.format_exc())
        return jsonify({"status": "error", "message": "QC save failed. Please try again."}), 500


@app.route('/api/apply-qc-map', methods=['POST'])
@login_required
def api_apply_qc_map():
    # Viewer cannot apply QC map
    if not is_write_allowed():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /api/apply-qc-map.",
            session.get('user'), session.get('role')
        )
        return jsonify({"error": "Access denied"}), 403
    
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
        logger.error("[api_apply_qc_map] Error: %s", e, exc_info=True)
        return jsonify({"status":"error","message":"An error occurred while applying QC results. Please try again."}), 500


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


# NEW: QC Inspection Traceability API
@app.route('/api/qc-traceability/<int:inspection_id>', methods=['GET'])
@login_required
def api_qc_traceability(inspection_id):
    """
    Retrieve full traceability information for a QC inspection.
    Returns: GRN details, Invoice details, Supplier info, Product info
    """
    conn = get_db_connection()
    try:
        # Fetch inspection with full join chain to GRN -> Invoice -> Supplier
        inspection = conn.execute("""
            SELECT
                ie.id AS inspection_id,
                ie.inspection_date,
                ie.product_id,
                ie.grn_item_id,
                p.item_code,
                p.item_name,
                p.barcode,
                gi.quantity AS grn_quantity,
                gi.batch_no,
                gi.expiry_date,
                gi.unit_price,
                gi.qc_status,
                g.grn_no,
                g.received_date,
                g.status AS grn_status,
                inv.invoice_number,
                inv.invoice_date,
                inv.vendor_name,
                inv.total_amount,
                s.id AS supplier_id,
                s.supplier_name,
                s.gst_number,
                s.contact_person,
                s.phone,
                s.email,
                s.address
            FROM inspection_entries ie
            JOIN products p ON ie.product_id = p.id
            LEFT JOIN grn_items gi ON ie.grn_item_id = gi.id
            LEFT JOIN grn g ON gi.grn_id = g.id
            LEFT JOIN invoices inv ON g.invoice_id = inv.id
            LEFT JOIN suppliers s ON inv.supplier_id = s.id
            WHERE ie.id = ?
        """, (inspection_id,)).fetchone()

        if not inspection:
            return jsonify({"error": "Inspection not found"}), 404

        # Fetch inspection details
        details = conn.execute("""
            SELECT
                id.obs1, id.obs2, id.obs3, id.obs4, id.obs5, id.remarks,
                pp.property_name, pp.min_value, pp.max_value, pp.method
            FROM inspection_details id
            JOIN product_properties pp ON id.product_property_id = pp.id
            WHERE id.inspection_id = ?
            ORDER BY pp.id
        """, (inspection_id,)).fetchall()

        result = {
            "inspection": dict(inspection),
            "details": [dict(d) for d in details],
            "traceability": {
                "has_grn_link": inspection['grn_item_id'] is not None,
                "grn_no": inspection['grn_no'],
                "invoice_number": inspection['invoice_number'],
                "supplier_name": inspection['supplier_name'],
                "supplier_gst": inspection['gst_number'],
                "received_date": inspection['received_date'],
            }
        }

        return jsonify(result)

    except Exception as e:
        logger.error("[api_qc_traceability] Error: %s", e, exc_info=True)
        return jsonify({"error": "Failed to fetch traceability data"}), 500
    finally:
        conn.close()


# NEW: List QC Inspections with Traceability
@app.route('/api/qc-inspections', methods=['GET'])
@login_required
def api_qc_inspections():
    """
    List all QC inspections with basic traceability info.
    Optional query params: product_id, grn_id, supplier_id, from_date, to_date
    """
    product_id = request.args.get('product_id', type=int)
    grn_id = request.args.get('grn_id', type=int)
    supplier_id = request.args.get('supplier_id', type=int)
    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()

    conn = get_db_connection()
    try:
        query = """
            SELECT
                ie.id AS inspection_id,
                ie.inspection_date,
                p.item_code,
                p.item_name,
                gi.qc_status,
                g.grn_no,
                inv.invoice_number,
                s.supplier_name
            FROM inspection_entries ie
            JOIN products p ON ie.product_id = p.id
            LEFT JOIN grn_items gi ON ie.grn_item_id = gi.id
            LEFT JOIN grn g ON gi.grn_id = g.id
            LEFT JOIN invoices inv ON g.invoice_id = inv.id
            LEFT JOIN suppliers s ON inv.supplier_id = s.id
            WHERE 1=1
        """
        params = []

        if product_id:
            query += " AND ie.product_id = ?"
            params.append(product_id)

        if grn_id:
            query += " AND gi.grn_id = ?"
            params.append(grn_id)

        if supplier_id:
            query += " AND inv.supplier_id = ?"
            params.append(supplier_id)

        if from_date:
            query += " AND ie.inspection_date >= ?"
            params.append(from_date)

        if to_date:
            query += " AND ie.inspection_date <= ?"
            params.append(to_date)

        query += " ORDER BY ie.inspection_date DESC, ie.id DESC"

        inspections = conn.execute(query, params).fetchall()

        return jsonify({
            "inspections": [dict(i) for i in inspections]
        })

    except Exception as e:
        logger.error("[api_qc_inspections] Error: %s", e, exc_info=True)
        return jsonify({"error": "Failed to fetch inspections"}), 500
    finally:
        conn.close()

    
# ===========================
# BARCODE SEARCH API
# ===========================

@app.route('/api/search_barcode', methods=['POST'])
@login_required
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
@login_required
def api_ai_scan_barcode():
    if 'image' not in request.files:
        return jsonify({'success': False, 'message': 'No image file uploaded.'}), 400

    image_file = request.files['image']
    if image_file.filename == '':
        return jsonify({'success': False, 'message': 'No selected image file.'}), 400

    # Validate extension — only common image formats accepted
    allowed_image_exts = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
    ext = os.path.splitext(image_file.filename)[1].lower()
    if ext not in allowed_image_exts:
        return jsonify({'success': False, 'message': 'Invalid image format. Upload JPG, PNG, or WEBP.'}), 400

    # Use a safe server-generated temp filename
    safe_temp = os.path.join(app.root_path, 'static', f'tmp_barcode_{secrets.token_hex(8)}{ext}')
    image_file.save(safe_temp)

    extracted_code = ""
    try:
        # Pass the image to the AI logic in gemini_extractor
        result_schema = ai.extract_barcode_data(safe_temp)
        extracted_code = result_schema.barcode_text.strip()
    except Exception as e:
        if os.path.exists(safe_temp):
            os.remove(safe_temp)
        return jsonify({'success': False, 'message': 'AI extraction failed. Please try again.'}), 500

    # Clean up file
    if os.path.exists(safe_temp):
        os.remove(safe_temp)

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
@login_required
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
@login_required
def scanner_demo():
    return render_template("scanner_demo.html")

@app.route('/confirm-grn/<int:grn_id>', methods=['POST'])
@login_required
def confirm_grn(grn_id):
    # Viewer cannot confirm GRN
    if not is_write_allowed():
        logger.warning(
            "[RBAC] User '%s' (role=%s) attempted POST on /confirm-grn.",
            session.get('user'), session.get('role')
        )
        return jsonify({"status": "error", "message": "Access denied"}), 403
    
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
        conn.isolation_level = None   # autocommit off — we manage transaction manually
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

        try:
            archived_items = cursor.execute("""
                SELECT gi.quantity, p.item_name, p.item_code, s.supplier_name
                FROM grn_items gi
                JOIN products p ON gi.product_id = p.id
                LEFT JOIN grn g ON gi.grn_id = g.id
                LEFT JOIN suppliers s ON g.supplier_id = s.id
                WHERE gi.grn_id = ? AND LOWER(COALESCE(gi.qc_status, '')) = 'confirmed'
            """, (grn_id,)).fetchall()
            for item in archived_items:
                create_qr_report_archive({
                    'grn_number': result_extra.get('grn_no') or grn_row['grn_no'],
                    'item_name': item['item_name'],
                    'item_code': item['item_code'],
                    'supplier_name': item['supplier_name'] or 'N/A',
                    'quantity': item['quantity'] or 0,
                    'generated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                    'downloaded_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                    'downloaded_by': session.get('user') or 'system',
                })
        except Exception as archive_exc:
            logger.warning('[confirm_grn] Unable to archive QR report history for GRN %s: %s', grn_id, archive_exc)

        resp = {"status": "success", "message": "GRN posted successfully."}
        resp.update(result_extra)
        return jsonify(resp)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"status": "error", "message": "An error occurred while posting the GRN. Please try again."}), 500
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
    """
    Password recovery — step 1.
    Accepts an email address. If a user with that email exists, generates a 
    one-time token, stores it with a 15-minute expiry, and sends the reset link.
    The response is always generic to prevent email enumeration.
    
    IMPORTANT: Token is ONLY generated and email is ONLY sent if the user exists.
    """
    if request.method == 'POST':
        raw_email = request.form.get('email', '').strip().lower()

        # Basic format validation (server-side guard)
        email_pattern = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
        if not raw_email or not email_pattern.match(raw_email):
            # Show generic message — don't help enumerate accounts
            flash(
                "If an account exists for this email, a password reset link has been sent.",
                "info"
            )
            return redirect(url_for('forgot_password'))

        try:
            with get_db_connection() as conn:
                # ✅ CRITICAL: First check if user exists with this email
                user = conn.execute(
                    "SELECT id, username, email FROM users WHERE LOWER(email) = ?",
                    (raw_email,)
                ).fetchone()

                # ✅ ONLY generate token and send email if user exists
                if user:
                    # User exists - proceed with token generation
                    token  = secrets.token_urlsafe(32)
                    expiry = time.time() + 15 * 60  # 15 minutes from now

                    # Store token in database
                    conn.execute(
                        "UPDATE users SET reset_token = ?, token_expiry = ? WHERE id = ?",
                        (token, expiry, user['id'])
                    )
                    conn.commit()

                    # Send reset email ONLY to registered users
                    reset_link = url_for('reset_password', token=token, _external=True)
                    sent = send_recovery_email(raw_email, user['username'], reset_link)
                    
                    if sent:
                        logger.info(
                            "[forgot_password] Reset link sent to registered user: %s (id=%s)",
                            user['username'], user['id']
                        )
                    else:
                        logger.warning(
                            "[forgot_password] Email delivery failed for user id=%s — "
                            "token was stored but email was not sent.", user['id']
                        )
                else:
                    # ✅ User does NOT exist - do nothing (no token, no email)
                    logger.info(
                        "[forgot_password] No user found with email: %s — no email sent",
                        raw_email
                    )
                
                # ✅ ALWAYS show the same generic message (prevents email enumeration)
                flash(
                    "If an account exists for this email, a password reset link has been sent.",
                    "info"
                )
                
        except Exception:
            logger.error("[forgot_password] Unhandled error", exc_info=True)
            flash(
                "If an account exists for this email, a password reset link has been sent.",
                "info"
            )

        return redirect(url_for('forgot_password'))

    return render_template("forgot_password.html")

@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    """
    Password recovery — step 2.
    Validates the one-time token, enforces password rules, hashes the new
    password, and clears the token so the link cannot be reused.
    """
    token = request.args.get('token', '').strip()
    if not token:
        flash("Invalid or missing reset token.", "error")
        return redirect(url_for('login'))

    try:
        with get_db_connection() as conn:
            user = conn.execute(
                "SELECT id, username FROM users WHERE reset_token = ? AND token_expiry > ?",
                (token, time.time())
            ).fetchone()

            if not user:
                flash("This password reset link has expired or has already been used.", "error")
                return redirect(url_for('login'))

            if request.method == 'POST':
                new_password     = request.form.get('password', '').strip()
                confirm_password = request.form.get('confirm_password', '').strip()

                # --- Validation ---
                if len(new_password) < 8:
                    flash("Password must be at least 8 characters long.", "error")
                    return render_template('reset_password.html', token=token)

                if new_password != confirm_password:
                    flash("Passwords do not match. Please try again.", "error")
                    return render_template('reset_password.html', token=token)

                # --- Persist hashed password; invalidate token (one-time use) ---
                conn.execute(
                    """UPDATE users
                       SET password_hash = ?,
                           reset_token   = NULL,
                           token_expiry  = NULL
                       WHERE id = ?""",
                    (generate_password_hash(new_password), user['id'])
                )
                conn.commit()

                logger.info("[reset_password] Password updated for user id=%s", user['id'])
                flash("Your password has been updated successfully. Please sign in.", "success")
                return redirect(url_for('login'))

    except Exception:
        logger.error("[reset_password] Unhandled error", exc_info=True)
        flash("An error occurred. Please request a new reset link.", "error")
        return redirect(url_for('forgot_password'))

    return render_template('reset_password.html', token=token)


# =====================================================
# ADDED: USER ADMINISTRATIVE EMAIL DIRECTORY PANEL
# =====================================================

@app.route('/admin/users-email', methods=['GET', 'POST'])
@login_required
def manage_users_emails():
    """Administrative email directory — admin only."""
    if not is_admin():
        return render_template('403.html'), 403

    conn = get_db_connection()
    if request.method == 'POST':
        user_id     = request.form.get('user_id')
        new_email   = request.form.get('email', '').strip()
        is_verified = request.form.get('verified') == '1'

        conn.execute(
            "UPDATE users SET email = ?, email_verified = ? WHERE id = ?",
            (new_email if new_email else None, 1 if is_verified else 0, user_id)
        )
        conn.commit()
        flash("User email updated successfully.", "success")

    all_users = conn.execute(
        "SELECT id, username, role, is_active, email, email_verified FROM users ORDER BY username ASC"
    ).fetchall()
    conn.close()

    return render_template('user_management.html', users=all_users, can_manage=True)


# =====================================================
# ADDED: MOBILE PHONE INVOICE SCANNING FLOW
# =====================================================

mobile_sessions = {}
_MOBILE_SESSION_TTL = 900  # 15 minutes

def _cleanup_mobile_sessions():
    """Remove expired mobile sessions to prevent unbounded memory growth."""
    now = time.time()
    expired = [sid for sid, s in list(mobile_sessions.items())
               if now - s.get('created_at', now) > _MOBILE_SESSION_TTL]
    for sid in expired:
        mobile_sessions.pop(sid, None)

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
        logger.error("[async_extract_task] session=%s error=%s", session_id, e, exc_info=True)
        mobile_sessions[session_id]["status"] = "error"
        mobile_sessions[session_id]["error"] = "Extraction failed. Please try again or enter details manually."
    finally:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

@app.route('/api/mobile-session/create', methods=['POST'])
@login_required
def create_mobile_session():
    import uuid
    import socket
    _cleanup_mobile_sessions()   # prune stale sessions first
    session_id = str(uuid.uuid4())
    mobile_sessions[session_id] = {
        "status": "pending",
        "payload": None,
        "error": None,
        "created_at": time.time()
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


# ─────────────────────────────────────────────────────────────────────────────
# DUPLICATE CHECK API  –  used by frontend validation before form submission
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/check-duplicate', methods=['POST'])
@login_required
def api_check_duplicate():
    """
    Generic duplicate-check endpoint.
    Body JSON: { "type": "<check_type>", "value": "<value>", [extra fields] }

    Supported types:
      product_code    – check products.item_code
      product_name    – check products.item_name
      gst_number      – check suppliers.gst_number
      dept_id         – check departments.dept_id
      dept_name       – check departments.dept_name
      username        – check users.username
      invoice_number  – check invoices.invoice_number
    """
    data  = request.get_json(force=True) or {}
    dtype = (data.get('type') or '').strip()
    value = (data.get('value') or '').strip()

    if not dtype or not value:
        return jsonify({'exists': False, 'message': ''}), 200

    conn = get_db_connection()
    try:
        exists = False
        detail = ''

        if dtype == 'product_code':
            row = conn.execute(
                "SELECT item_code, item_name FROM products WHERE LOWER(item_code) = LOWER(?) LIMIT 1",
                (value,)
            ).fetchone()
            if row:
                exists = True
                detail = f"Item Code <strong>{row['item_code']}</strong> is already registered as <em>{row['item_name']}</em>."

        elif dtype == 'product_name':
            row = conn.execute(
                "SELECT item_code, item_name FROM products WHERE LOWER(item_name) = LOWER(?) LIMIT 1",
                (value,)
            ).fetchone()
            if row:
                exists = True
                detail = f"Product Name <strong>{row['item_name']}</strong> already exists under code <em>{row['item_code']}</em>."

        elif dtype == 'gst_number':
            row = conn.execute(
                "SELECT supplier_name, gst_number FROM suppliers WHERE UPPER(gst_number) = UPPER(?) LIMIT 1",
                (value,)
            ).fetchone()
            if row:
                exists = True
                detail = f"GST Number <strong>{row['gst_number']}</strong> already belongs to supplier <em>{row['supplier_name']}</em>."

        elif dtype == 'dept_id':
            row = conn.execute(
                "SELECT dept_id, dept_name FROM departments WHERE LOWER(dept_id) = LOWER(?) LIMIT 1",
                (value,)
            ).fetchone()
            if row:
                exists = True
                detail = f"Department ID <strong>{row['dept_id']}</strong> is already in use by <em>{row['dept_name']}</em>."

        elif dtype == 'dept_name':
            row = conn.execute(
                "SELECT dept_id, dept_name FROM departments WHERE LOWER(dept_name) = LOWER(?) LIMIT 1",
                (value,)
            ).fetchone()
            if row:
                exists = True
                detail = f"Department Name <strong>{row['dept_name']}</strong> already exists with ID <em>{row['dept_id']}</em>."

        elif dtype == 'username':
            row = conn.execute(
                "SELECT username FROM users WHERE LOWER(username) = LOWER(?) LIMIT 1",
                (value,)
            ).fetchone()
            if row:
                exists = True
                detail = f"Username <strong>{row['username']}</strong> is already taken."

        elif dtype == 'email':
            row = conn.execute(
                "SELECT username FROM users WHERE LOWER(email) = LOWER(?) LIMIT 1",
                (value,)
            ).fetchone()
            if row:
                exists = True
                detail = f"Email address <strong>{value}</strong> is already registered to another account."

        elif dtype == 'invoice_number':
            row = conn.execute(
                "SELECT invoice_number, invoice_date, vendor_name FROM invoices WHERE LOWER(invoice_number) = LOWER(?) LIMIT 1",
                (value,)
            ).fetchone()
            if row:
                exists = True
                detail = (
                    f"Invoice <strong>{row['invoice_number']}</strong> was already posted"
                    + (f" on {row['invoice_date']}" if row['invoice_date'] else '')
                    + (f" from <em>{row['vendor_name']}</em>" if row['vendor_name'] else '')
                    + ". Posting again will create <strong>duplicate inventory records</strong>."
                )

        else:
            return jsonify({'exists': False, 'message': 'Unknown check type'}), 200

        return jsonify({'exists': exists, 'detail': detail})

    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# STOCK CHECK API  –  validate available qty before item issue
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/check-stock', methods=['POST'])
@login_required
def api_check_stock():
    """
    Check whether requested qty is available for each product.
    Body JSON: { "items": [ { "product_id": int, "qty": float }, ... ] }
    Returns: { "ok": bool, "errors": [ { "item_code", "item_name", "available", "requested" }, ... ] }
    """
    data  = request.get_json(force=True) or {}
    items = data.get('items') or []

    if not items:
        return jsonify({'ok': True, 'errors': []}), 200

    conn = get_db_connection()
    errors = []
    try:
        for item in items:
            pid = item.get('product_id')
            req = float(item.get('qty') or 0)
            if not pid or req <= 0:
                continue
            row = conn.execute(
                "SELECT item_code, item_name, COALESCE(current_stock,0) AS current_stock FROM products WHERE id = ?",
                (int(pid),)
            ).fetchone()
            if not row:
                continue
            avail = float(row['current_stock'])
            if req > avail:
                errors.append({
                    'item_code':  row['item_code'],
                    'item_name':  row['item_name'],
                    'available':  avail,
                    'requested':  req,
                })
    finally:
        conn.close()

    return jsonify({'ok': len(errors) == 0, 'errors': errors})


# ─────────────────────────────────────────────────────────────────────────────
# SUPPLIER LOOKUP API  –  fetch supplier by GST number
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/supplier-by-gst', methods=['GET'])
@login_required
def api_supplier_by_gst():
    """
    Fetch supplier details by GST number.
    Query param: gst (required)
    Returns: { "id", "supplier_name", "gst_number", "contact_person", "phone", "email", "address" }
    """
    gst = request.args.get('gst', '').strip().upper()
    if not gst:
        return jsonify({'error': 'GST number required'}), 400

    conn = get_db_connection()
    try:
        supplier = conn.execute("""
            SELECT id, supplier_name, gst_number, contact_person, phone, email, address
            FROM suppliers
            WHERE UPPER(TRIM(gst_number)) = ?
        """, (gst,)).fetchone()

        if supplier:
            return jsonify(dict(supplier))
        else:
            return jsonify({'error': 'Supplier not found'}), 404
    except Exception as e:
        logger.error("[api_supplier_by_gst] Error: %s", e, exc_info=True)
        return jsonify({'error': 'Failed to fetch supplier'}), 500
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/supplier/<int:supplier_id>', methods=['GET'])
@login_required
def api_supplier_by_id(supplier_id):
    """
    Fetch supplier details by ID.
    Returns: { "id", "supplier_name", "gst_number", "contact_person", "phone", "email", "address" }
    """
    conn = get_db_connection()
    try:
        supplier = conn.execute("""
            SELECT id, supplier_name, gst_number, contact_person, phone, email, address
            FROM suppliers
            WHERE id = ?
        """, (supplier_id,)).fetchone()

        if supplier:
            return jsonify(dict(supplier))
        else:
            return jsonify({'error': 'Supplier not found'}), 404
    except Exception as e:
        logger.error("[api_supplier_by_id] Error: %s", e, exc_info=True)
        return jsonify({'error': 'Failed to fetch supplier'}), 500
    finally:
        conn.close()



if __name__ == '__main__':
    # debug=False in production — set HOST/PORT via environment if needed
    app.run(debug=False, port=int(os.getenv('PORT', 5000)), host='0.0.0.0')


