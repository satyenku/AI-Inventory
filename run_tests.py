import os
import sys
import sqlite3
import pandas as pd
from init_db import init_db
from extractor import extract_text_from_file, parse_invoice_data, normalize_date
from config import Config

# Helper to execute shell-like operations programmatically
def run_invoice_generation():
    import subprocess
    print("Executing generate_test_invoices.py...")
    result = subprocess.run([sys.executable, "generate_test_invoices.py"], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print("Error generating test invoices:", result.stderr)
        sys.exit(1)

def test_pipeline():
    print("====================================================")
    print("   EXTRACTOAI - SYSTEM TEST HARNESS & CHECK RUNNER ")
    print("====================================================")
    
    # 1. Generate test invoices
    run_invoice_generation()
    
    # 2. Reset and initialize database
    if os.path.exists(Config.DATABASE_PATH):
        try:
            os.remove(Config.DATABASE_PATH)
            print("Removed existing invoices.db for fresh test initialization.")
        except Exception as e:
            print(f"Could not remove database file: {e}")
            
    init_db()
    
    # 3. Test Cases (Mock Invoice Expectations)
    test_cases = [
        {
            "filename": "invoice_tech_solutions.png",
            "expected_vendor": "TECH SOLUTIONS INC.",
            "expected_invoice_number": "INV-2026-0042",
            "expected_date": "2026-05-12",
            "expected_tax": 45.00,
            "expected_total": 545.00
        },
        {
            "filename": "invoice_global_logistics.png",
            "expected_vendor": "GLOBAL LOGISTICS CO.",
            "expected_invoice_number": "GLB987654",
            "expected_date": "2026-05-25",
            "expected_tax": 120.50,
            "expected_total": 1620.50
        },
        {
            "filename": "invoice_apex_marketing.png",
            "expected_vendor": "APEX MARKETING GROUP",
            "expected_invoice_number": "AMP-9921",
            "expected_date": "2026-06-01",
            "expected_tax": 21.00,
            "expected_total": 321.00
        }
    ]
    
    # Verify Tesseract is running
    print(f"Tesseract Path configured: {Config.TESSERACT_CMD}")
    if not os.path.exists(Config.TESSERACT_CMD):
        print(f"WARNING: Tesseract OCR executable not found at {Config.TESSERACT_CMD}.")
        print("Automated test run will be simulated or skipped unless Tesseract is installed.")
        print("Proceeding to test regex extractor functions directly with mock OCR text first...")
    
    passed_tests = 0
    total_tests = len(test_cases)
    
    # Check 1: Date Normalization Unit Tests
    print("\n--- Running Date Normalization Unit Tests ---")
    date_tests = [
        ("May 12, 2026", "2026-05-12"),
        ("25/05/2026", "2026-05-25"),
        ("2026-06-01", "2026-06-01"),
        ("12/05/26", "2026-05-12"),
        ("NOT FOUND", "NOT FOUND")
    ]
    for raw, expected in date_tests:
        norm = normalize_date(raw)
        if norm == expected:
            print(f"  [PASS] '{raw}' normalized to '{norm}'")
        else:
            print(f"  [FAIL] '{raw}' normalized to '{norm}', expected '{expected}'")
            
    # Check 2: Core Processing & Database Ingestion
    print("\n--- Running Ingestion & Extraction Pipeline ---")
    conn = sqlite3.connect(Config.DATABASE_PATH)
    cursor = conn.cursor()
    
    for case in test_cases:
        file_path = os.path.join(Config.UPLOAD_FOLDER, case["filename"])
        print(f"\nProcessing Invoice: {case['filename']}")
        
        # Extract text and parse
        try:
            raw_text = extract_text_from_file(file_path)
            # Display a snippet of raw text
            snippet = raw_text.replace('\n', ' ')[:100]
            print(f"  Raw OCR Text Snippet: \"{snippet}...\"")
            
            parsed = parse_invoice_data(raw_text)
            
            # Print parsed values
            print(f"  Extracted Vendor:  {parsed['vendor_name']}")
            print(f"  Extracted Inv No:  {parsed['invoice_number']}")
            print(f"  Extracted Date:    {parsed['invoice_date']}")
            print(f"  Extracted Tax:     {parsed['tax_amount']}")
            print(f"  Extracted Total:   {parsed['total_amount']}")
            
            # Format outputs safely
            try:
                tax_val = float(parsed["tax_amount"].replace(',', ''))
            except ValueError:
                tax_val = 0.00
                
            try:
                total_val = float(parsed["total_amount"].replace(',', ''))
            except ValueError:
                total_val = 0.00
                
            # Perform assertions
            vendor_ok = parsed["vendor_name"].upper() == case["expected_vendor"].upper()
            inv_ok = parsed["invoice_number"] == case["expected_invoice_number"]
            date_ok = parsed["invoice_date"] == case["expected_date"]
            tax_ok = abs(tax_val - case["expected_tax"]) < 0.01
            total_ok = abs(total_val - case["expected_total"]) < 0.01
            
            if vendor_ok and inv_ok and date_ok and tax_ok and total_ok:
                print(f"  [PASS] All fields successfully extracted!")
                passed_tests += 1
            else:
                print(f"  [FAIL] Extraction discrepancies detected:")
                if not vendor_ok: print(f"    Expected Vendor '{case['expected_vendor']}', got '{parsed['vendor_name']}'")
                if not inv_ok: print(f"    Expected Invoice No '{case['expected_invoice_number']}', got '{parsed['invoice_number']}'")
                if not date_ok: print(f"    Expected Date '{case['expected_date']}', got '{parsed['invoice_date']}'")
                if not tax_ok: print(f"    Expected Tax '{case['expected_tax']}', got '{tax_val}'")
                if not total_ok: print(f"    Expected Total '{case['expected_total']}', got '{total_val}'")
                
            # Insert into database to check schema constraints
            cursor.execute('''
                INSERT INTO invoices (vendor_name, invoice_number, invoice_date, tax_amount, total_amount, file_name)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (parsed["vendor_name"], parsed["invoice_number"], parsed["invoice_date"], tax_val, total_val, case["filename"]))
            
        except Exception as e:
            print(f"  [CRITICAL ERROR] Failed to process invoice: {e}")
            
    conn.commit()
    
    # Check 3: Database Log Counts
    print("\n--- Verifying Database Entries ---")
    cursor.execute("SELECT COUNT(*) FROM invoices")
    count = cursor.fetchone()[0]
    print(f"  Total records stored in database: {count}")
    if count == len(test_cases):
        print("  [PASS] Database storage validated.")
    else:
        print("  [FAIL] Database record mismatch.")
        
    # Check 4: Dashboard Matrix Filtering Logic Simulation
    print("\n--- Running Search & Filtering Simulations ---")
    
    # Query by Vendor
    cursor.execute("SELECT * FROM invoices WHERE vendor_name LIKE ?", ('%Solutions%',))
    rows = cursor.fetchall()
    print(f"  Filter [Vendor LIKE '%Solutions%']: {len(rows)} matching record(s)")
    if len(rows) > 0 and "INV-2026-0042" in [r[2] for r in rows]:
        print("  [PASS] Vendor name search filter verified.")
    else:
        print("  [FAIL] Vendor name search filter query failed.")
        
    # Query by Invoice Number
    cursor.execute("SELECT * FROM invoices WHERE invoice_number LIKE ?", ('%GLB%',))
    rows = cursor.fetchall()
    print(f"  Filter [Invoice # LIKE '%GLB%']: {len(rows)} matching record(s)")
    if len(rows) > 0 and "GLB987654" in [r[2] for r in rows]:
         print("  [PASS] Invoice Number search filter verified.")
    else:
        print("  [FAIL] Invoice Number search filter query failed.")
        
    conn.close()
    
    # Check 5: Pandas Export Verification
    print("\n--- Running Pandas CSV Export Validation ---")
    try:
        conn = sqlite3.connect(Config.DATABASE_PATH)
        df = pd.read_sql_query("SELECT * FROM invoices", conn)
        conn.close()
        
        export_path = "test_audit_export.csv"
        df.to_csv(export_path, index=False)
        print(f"  [PASS] Table successfully exported to: {export_path}")
        
        # Verify export exists and has content
        if os.path.exists(export_path) and os.path.getsize(export_path) > 100:
            print("  [PASS] Exported spreadsheet content size verified.")
            os.remove(export_path) # cleanup
        else:
            print("  [FAIL] Exported spreadsheet file is missing or empty.")
    except Exception as e:
         print(f"  [FAIL] Pandas Excel/CSV export sequence failed: {e}")
         
    print("\n====================================================")
    print(f"  CHECK SUMMARIES: Passed {passed_tests} / {total_tests} Complete Pipelines.")
    print("====================================================")

if __name__ == '__main__':
    test_pipeline()
