from flask import Flask, render_template, request, jsonify
import sqlite3
import qrcode
import uuid
import os

app = Flask(__name__)

DB = "qr_demo.db"
QR_FOLDER = "static/qr_codes"

os.makedirs(QR_FOLDER, exist_ok=True)


# ==========================
# Database Initialization
# ==========================

def init_db():

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS qr_registry(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        qr_code TEXT UNIQUE,

        product_name TEXT,

        qr_image TEXT

    )
    """)

    conn.commit()
    conn.close()


init_db()


# ==========================
# Home Page
# ==========================

@app.route("/")
def home():

    return render_template("index.html")


# ==========================
# Generate QR
# ==========================

@app.route("/generate", methods=["POST"])
def generate():

    product = request.form["product"]

    qr_code = "QR-" + uuid.uuid4().hex[:8].upper()

    img = qrcode.make(qr_code)

    filename = qr_code + ".png"

    filepath = os.path.join(QR_FOLDER, filename)

    img.save(filepath)

    conn = sqlite3.connect(DB)

    cur = conn.cursor()

    cur.execute("""

    INSERT INTO qr_registry
    (qr_code,product_name,qr_image)

    VALUES(?,?,?)

    """,(qr_code,product,filepath))

    conn.commit()

    conn.close()

    return render_template(

        "index.html",

        qr=filepath,

        code=qr_code,

        product=product

    )


# ==========================
# Scan Page
# ==========================

@app.route("/scan")
def scan():

    return render_template("scan.html")


# ==========================
# Search Product using QR
# ==========================

@app.route("/search_qr", methods=["POST"])
def search_qr():

    data = request.get_json()

    qr_code = data["qr"]

    conn = sqlite3.connect(DB)

    cur = conn.cursor()

    cur.execute("""

    SELECT
    product_name,
    qr_code

    FROM qr_registry

    WHERE qr_code=?

    """,(qr_code,))

    row = cur.fetchone()

    conn.close()

    if row:

        return jsonify({

            "status":"success",

            "product":row[0],

            "qr":row[1]

        })

    else:

        return jsonify({

            "status":"failed"

        })


# ==========================
# Main
# ==========================

if __name__=="__main__":

    app.run(debug=True)