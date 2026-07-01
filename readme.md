# AI Inventory — Invoice Reader & Stock Management System

An AI-powered inventory management system for AC parts businesses. Upload invoices as PDFs and Gemini AI automatically extracts all fields, creates stock entries, generates barcodes, and tracks every movement through a full audit trail.

---

## What the system does

- Upload any supplier invoice (PDF) — Gemini AI reads it and fills in all fields automatically
- Manages the complete stock lifecycle: GRN → Issue → Return, with a ledger entry at every step
- Generates unique barcodes for every GRN line item — scan to identify supplier on returns
- Runs quality control inspection sheets per product with configurable dimensional and visual specs
- Multi-user login with role-based access (Admin / Staff / Viewer)
- Export inventory reports and QC sheets

---

## Project structure

```
final_v2/
├── app.py                    ← Flask application — all routes
├── config.py                 ← Paths and environment variable config
├── db_helpers.py             ← Database connection and stock movement helpers
├── init_db.py                ← Database schema — run once to create all tables
├── gemini_extractor.py       ← Gemini AI invoice extraction with model fallback
├── generate_invoices.py      ← Test invoice PDF generator (30 AC parts invoices)
├── .env                      ← Your API key (create this — never commit to git)
├── .gitignore                ← Already excludes .env and uploads
├── inventory.db              ← SQLite database (auto-created by init_db.py)
├── uploads/                  ← Temp folder for invoice files during processing
├── static/
│   └── barcodes/             ← Generated barcode PNG files
└── templates/
    ├── login.html
    ├── dashboard.html
    ├── product_master.html
    ├── supplier_management.html
    ├── item_entry.html       ← Main invoice upload + GRN creation page
    ├── item_issue.html
    ├── inventory_return.html ← Barcode scan → auto-fills supplier details
    ├── inventory_status.html
    ├── inspection_entry.html ← QC inspection form
    ├── qc_sheet.html
    └── user_management.html
```

---

## Requirements

### System requirements

**Python 3.11** is recommended. The project runs on 3.11, 3.12, and 3.13.

**poppler** — required for PDF processing:

```bash
# Mac
brew install poppler

# Ubuntu / Debian
sudo apt install poppler-utils

# Windows
# Download from: https://github.com/oschwartz10612/poppler-windows/releases
# Extract and add the bin/ folder to your system PATH
```

### Python packages

```
flask
python-dotenv
google-genai
pydantic
pillow
python-barcode
reportlab
faker
```

Full install command is in the setup steps below.

---

## Setup — step by step

### 1. Clone or extract the project

```bash
# If cloning from git
git clone https://github.com/yourusername/AI-Inventory.git
cd AI-Inventory/final_v2

# If using the zip
unzip final_v2.zip
cd final_v2
```

### 2. Create a virtual environment

```bash
python3 -m venv venv

# Mac / Linux
source venv/bin/activate

# Windows
venv\Scripts\activate
```

You should see `(venv)` at the start of your terminal prompt.

### 3. Install Python dependencies

```bash
pip install flask python-dotenv google-genai pydantic pillow python-barcode reportlab faker
```

### 4. Get a free Gemini API key

1. Go to **aistudio.google.com**
2. Sign in with a Google account
3. Click **Get API Key → Create API key in new project**
4. Copy the key — it starts with `AIzaSy`

**Important:** Make sure the Google Cloud project linked to this key does **not** have billing enabled. The free tier only works on non-billing projects. If you enable billing, all requests become paid immediately.

### 5. Create the `.env` file

Create a file called `.env` inside the `final_v2/` folder:

```bash
# Mac / Linux
touch .env
```

Open it in any text editor and add exactly this — no quotes, no spaces around the `=`:

```
GEMINI_API_KEY=AIzaSyYourActualKeyHere
FLASK_SECRET_KEY=change_this_to_any_random_string
```

**Never commit this file to git.** The `.gitignore` already excludes it. If you accidentally commit it, GitHub will block your push and Google will automatically invalidate the key — you will need to create a new one.

### 6. Initialise the database

```bash
python init_db.py
```

This creates `inventory.db` with all 16 tables. Run this only once. If you run it again on an existing database it is safe — all statements use `CREATE TABLE IF NOT EXISTS`.

### 7. Run the application

```bash
python app.py
```

Open your browser at: **http://localhost:5000**

---

## First login

The system creates a default admin account on first run:

| Username | Password   |
| -------- | ---------- |
| `admin`  | `admin123` |

Go to **Users** in the sidebar immediately after first login and change the admin password.

---

## How to use each page

### Dashboard

Overview of total products, recent GRNs, and stock movement summary.

### Product Master (`/products`)

Add and manage your product/item catalogue. Each product has:

- Item code (unique SKU)
- Name, category, unit of measurement
- Min/max stock levels
- Inspection specs — click **⚙ Specs** on any product to define dimensional and visual inspection criteria. These load automatically on every inspection form for that product.

### Supplier Management (`/suppliers`)

Add supplier companies before processing invoices. The system matches the vendor name on an uploaded invoice to your supplier list to link `supplier_id`. If no match is found the invoice still saves but without a supplier link — you can see this on the inventory return page when scanning barcodes.

### Item Entry (`/item-entry`)

The main page for receiving stock.

1. Click **Upload Invoice** and select a PDF
2. Click **Extract with Gemini** — wait 10–30 seconds while AI reads the invoice
3. Review the extracted fields and line items
4. Select the correct supplier from the dropdown
5. Click **Confirm and Post GRN**

Each line item gets a unique barcode in the format `INVOICENO-LINENUM-TIMESTAMP`. The barcode strip appears after saving — print it and attach each barcode label to the physical item.

### Item Issue (`/item-issue`)

Record stock going out. Select products, enter quantities, specify department or work order. Every issue is recorded in the stock ledger and reduces current stock.

### Inventory Return (`/inventory-return`)

Record items coming back into stock.

Scan or type the GRN barcode from the item's label in the **Scan GRN Barcode** field at the top. The system will automatically show:

- Which supplier the item came from
- Original invoice number and date
- Supplier contact details and address

This tells you exactly who to return a defective item to without looking anything up manually.

### Inventory Status (`/inventory-status`)

Stock report across all products. Shows current stock, movements, and items below reorder level.

### Inspection Entry (`/inspection_entry`)

Quality control inspection form for incoming materials. Loads the specs you defined in Product Master and presents a table with 5 observation columns per spec row. Readings outside tolerance are highlighted red automatically. Mark the lot as Accepted or Rejected and save.

### Users (`/users`)

Admin-only page. Create, activate/deactivate, reset passwords, and delete user accounts. The system prevents deletion of the last admin account.

---

## Generating test invoices

The project includes 30 pre-generated test invoices in `test_invoices/`. These are AC parts invoices from 6 different suppliers, with the same parts intentionally appearing across multiple suppliers to test deduplication and supplier linking.

To generate a fresh set:

```bash
python generate_invoices.py
```

To test the full pipeline:

1. Add the 6 supplier companies in Supplier Management first (see supplier names in `generate_invoices.py` under `SUPPLIERS`)
2. Upload invoices one by one through the Item Entry page
3. Verify that the same part from two different suppliers creates one product record but two separate GRN records

---

## Database tables

| Table                    | Purpose                                          |
| ------------------------ | ------------------------------------------------ |
| `users`                  | Login accounts with roles                        |
| `suppliers`              | Supplier master list                             |
| `products`               | Product/item catalogue                           |
| `invoices`               | Invoice headers extracted by Gemini              |
| `invoice_items`          | Line items per invoice                           |
| `grn`                    | Goods Receipt Notes (one per confirmed invoice)  |
| `grn_items`              | Items within each GRN                            |
| `item_issues`            | Stock-out records                                |
| `item_issue_items`       | Items within each issue                          |
| `inventory_returns`      | Stock return records                             |
| `inventory_return_items` | Items within each return                         |
| `stock_ledger`           | Complete audit trail of every stock movement     |
| `barcode_registry`       | Maps each unique GRN barcode to its invoice item |
| `product_properties`     | Flexible inspection spec definitions per product |
| `inspection_entries`     | QC inspection header records                     |
| `inspection_details`     | Individual observation readings per inspection   |

---

## Gemini API — free tier limits

| Model tried (in order) | Requests/day | Requests/min |
| ---------------------- | ------------ | ------------ |
| gemini-2.5-flash-lite  | 1,500        | 15           |
| gemini-3.5-flash-lite  | 1,500        | 15           |
| gemini-3.5-flash       | 1,500        | 10           |
| gemini-2.5-flash       | 1,500        | 10           |

The extractor automatically tries each model in order if the previous one fails (overloaded, rate-limited, or unavailable). For single-page invoices this is 1,500 invoices per day free. Multi-page PDFs use one request per page.

---

## Troubleshooting

**401 UNAUTHENTICATED error from Gemini**

This almost always means one of:

- Your API key in `.env` has been invalidated — create a new one at aistudio.google.com
- The `.env` file has quotes or spaces around the key — it must be `GEMINI_API_KEY=AIzaSy...` with no spaces or quotes
- A Google Cloud environment variable is overriding your key. Run `echo $GOOGLE_APPLICATION_CREDENTIALS` — if it prints anything, run `unset GOOGLE_APPLICATION_CREDENTIALS` then restart Flask
- The key was accidentally committed to git — GitHub scans for secrets and Google automatically invalidates exposed keys. Create a new key and do not commit `.env`

**"All processing attempts failed"**

Gemini API is temporarily overloaded. Wait 30–60 seconds and try uploading the same invoice again.

**Barcode image not showing**

The `static/barcodes/` folder must be writable. Check permissions:

```bash
chmod 755 static/barcodes/
```

**`python-barcode` install error on Mac**

```bash
brew install zbar
pip install python-barcode pillow
```

**Port 5000 already in use**

```bash
# Mac — AirPlay Receiver uses port 5000. Either disable it in
# System Settings → General → AirDrop & Handoff, or run on a different port:
python app.py --port 5001
```

Or edit the last line of `app.py`:

```python
app.run(debug=True, port=5001)
```

**Database locked error**

This can happen if Flask is run multiple times simultaneously. Stop all running instances and start fresh:

```bash
# Find and kill any running Flask processes
pkill -f "python app.py"
python app.py
```

**`init_db.py` fails with "no such table: config"**

Make sure you are running `init_db.py` from inside the `final_v2/` directory, not from a parent folder. The `config.py` import resolves relative to the working directory.

```bash
cd final_v2
python init_db.py
```

---

## Git — keeping secrets safe

The `.gitignore` already includes:

```
.env
*.env
**/.env
uploads/
__pycache__/
*.pyc
inventory.db
```

Never remove `.env` from `.gitignore`. If you accidentally commit it:

```bash
# Remove from git history
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch .env" \
  --prune-empty --tag-name-filter cat -- --all

# Force push
git push origin main --force

# Then immediately go to aistudio.google.com and create a new API key
```
