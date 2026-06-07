# ExtractoAI - Invoice Parsing Architecture

A comprehensive end-to-end web application that ingests scanned invoice imagery and PDFs, extracts structured financial metrics using intelligent OCR, and archives records into a streamlined SQLite database. Built for seamless audit trailing with advanced matrix filters and bulk CSV exports.

## Key Features

1. **Intelligent Document Ingestion Engine**:
   - Upload UI supporting drag-and-drop.
   - Handles standard image formats (PNG, JPG, JPEG) alongside native PDFs.
   - Built-in validation limits payloads strictly to approved extension schemas.

2. **Automated Data Extraction & Storage**:
   - Implements `pdfplumber` and `pytesseract` to harvest raw text natively.
   - Custom Regex-driven parsing module designed to detect complex elements like:
     - Vendor Entity Names
     - Invoice Identification Numbers
     - Document Issued Dates
     - Subtotal/Tax Variances
     - Total Transaction Amount
   - Real-time serialization directly into an integrated SQLite persistent state database.

3. **History Log Audit Dashboard**:
   - Access history logs spanning previous uploads cleanly across the `/dashboard` endpoint.
   - Dynamic search matrix permitting complex filters: Vendor Entity, Invoice Date Range, and Identifier matching.
   - **Export Data Strategy**: Instantly package filtered results out into a functional `.csv` spreadsheet for external software consumption.

## Requirements

Ensure your environment fulfills the required structural components before staging the application:

```text
flask
pdfplumber
pytesseract
pillow
```

*Note: Tesseract OCR binaries must be installed independently on the local machine system path to permit core text-reading protocols. Review `config.py` to target the exact path (`C:\\Program Files\\Tesseract-OCR\\tesseract.exe`).*

## Setup Instructions

**1. Create the Database State**
Initialize the relational tables by calling the initialization blueprint:
```bash
python init_db.py
```
*This command will auto-generate `invoices.db` locally locking in the primary tables.*

**2. Formulate App Directives**
Boot up the main server on loopback targeting Port `5050` explicitly to bypass potential conflicts:
```bash
python app.py
```

**3. Test Interface Elements**
- Open a browser and navigate to `http://127.0.0.1:5050/`.
- Upload sample imagery or flat PDFs to monitor structural parsing live.
- View historically recorded instances via the History Dashboard link. 

## Architectural Layout

- `app.py`: Contains Flask Routing logic, ingest validation, database commits, and CSV generation routes.
- `extractor.py`: Holds Tesseract connections, PDF breakdown logic, and complex regex queries.
- `database.py / init_db.py`: Establishes basic connection strings and the foundation schema generation.
- `config.py`: Points to environmental parameters and configuration overrides.
- `templates/`: Interface elements structured on TailwindCSS arrays representing Uploads and Database logs.
