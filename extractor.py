import os
import re
import pdfplumber
import pytesseract
from PIL import Image
from datetime import datetime
from config import Config

# Configure Tesseract path if it exists
if os.path.exists(Config.TESSERACT_CMD):
    pytesseract.pytesseract.tesseract_cmd = Config.TESSERACT_CMD

def normalize_date(date_str):
    """Parses a date string and converts it to a standard YYYY-MM-DD ISO format."""
    if not date_str or date_str in ["NOT FOUND", "UNKNOWN"]:
        return "NOT FOUND"
    
    # Clean up whitespace and punctuation
    cleaned = re.sub(r'\s+', ' ', date_str).strip()
    cleaned = cleaned.replace(',', '')
    
    # List of date formats to try parsing
    formats = [
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
        "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
        "%m-%d-%Y", "%m/%d/%Y", "%m.%d.%Y",
        "%d-%m-%y", "%d/%m/%y", "%d.%m.%y",
        "%m-%d-%y", "%m/%d/%y", "%m.%d.%y",
        "%d %b %Y", "%d %B %Y",
        "%b %d %Y", "%B %d %Y",
        "%d %b %y", "%d %B %y",
        "%b %d %y", "%B %d %y",
        "%Y%m%d"
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
            
    return cleaned

def clean_amount_str(amt_str):
    """Safely extracts a floating number string from a dirty amount string."""
    if not amt_str or amt_str in ["NOT FOUND", "UNKNOWN"]:
        return "0.00"
    
    # Strip everything except digits and decimal point
    cleaned = re.sub(r'[^\d\.]', '', amt_str)
    
    # If there are multiple periods, keep only the last one as decimal separator
    if cleaned.count('.') > 1:
        parts = cleaned.split('.')
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
        
    if not cleaned:
        return "0.00"
        
    try:
        val = float(cleaned)
        return f"{val:.2f}"
    except ValueError:
        return "0.00"

def extract_text_from_file(file_path):
    """Detects file extension and extracts all raw readable text characters."""
    ext = file_path.split('.')[-1].lower()
    text = ""
    
    if ext == 'pdf':
        try:
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
        except Exception as e:
            print(f"Error extracting native PDF text: {e}")
            
    elif ext in ['png', 'jpg', 'jpeg']:
        try:
            img = Image.open(file_path)
            text = pytesseract.image_to_string(img)
        except Exception as e:
            print(f"Error running Tesseract OCR on image: {e}")
            
    return text

def parse_invoice_data(raw_text):
    """Robust structural parsing capable of capturing complex invoice structures."""
    data = {
        "invoice_number": "NOT FOUND",
        "vendor_name": "UNKNOWN VENDOR",
        "invoice_date": "NOT FOUND",
        "tax_amount": "0.00",
        "total_amount": "0.00"
    }
    
    if not raw_text.strip():
        return data
        
    # Get lines but keep original spacing for column detection
    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
    full_blob = "\n".join(lines)

    # 1. Vendor Name Strategy: Company Suffix Boundary Scanner
    # Suffixes are structured so that optional periods are captured before the word boundaries close.
    company_suffixes = [
        r'\bINC\b\.?', r'\bCO\b\.?', r'\bCORP\b\.?', r'\bLTD\b\.?', r'\bGROUP\b', 
        r'\bLLC\b', r'\bSOLUTIONS\b', r'\bLOGISTICS\b', r'\bMARKETING\b', r'\bSYSTEMS\b'
    ]
    
    found_vendor = False
    for line in lines[:5]:
        line_clean = line.strip()
        if line_clean.startswith('|'):
            line_clean = line_clean.lstrip('| \t')
            
        for suffix in company_suffixes:
            # Match everything from the beginning of the line up to the company suffix keyword
            match = re.search(r'^.*?' + suffix, line_clean, re.IGNORECASE)
            if match:
                val = match.group(0).strip()
                # Exclude prefixes like "BILL TO" or "SHIP TO"
                if not any(val.lower().startswith(w) for w in ["bill to", "ship to", "to:", "invoice", "date", "po number"]):
                    data["vendor_name"] = val
                    found_vendor = True
                    break
        if found_vendor:
            break
            
    # Fallback to standard line scanner if no company suffixes are found
    if data["vendor_name"] == "UNKNOWN VENDOR":
        for line in lines[:6]:
            cols = [c.strip() for c in re.split(r'\s{2,}|\t', line) if c.strip()]
            if not cols:
                continue
            candidate = cols[0]
            if candidate.startswith('|'):
                candidate = candidate.lstrip('| \t')
            candidate_lower = candidate.lower()
            if any(kwd in candidate_lower for kwd in [
                "invoice", "order", "bill to", "ship to", "date", "qty", "to :", "to:", "from:", 
                "from :", "[", "]", "regn", "tax", "total", "due", "status", "paid", ".com", "www", 
                "@", "subtotal", "bank", "routing", "account", "swift", "description", "rate", "item"
            ]):
                continue
            candidate = re.sub(r'[\W_]+$', '', candidate).strip()
            if len(candidate) > 3 and not re.match(r'^[\W\d\s]+$', candidate):
                data["vendor_name"] = candidate
                break

    # 2. Invoice Number Scanner (with false positive keyword exclusion)
    inv_num_patterns = [
        r'(?:Invoice|Order|Ref|Statement|#)\s*(?:No\.?|Num|Number|Id|ID|#)?\s*[:\-#]?\s*([A-Za-z0-9-]+)',
    ]
    
    found_number = False
    for pat in inv_num_patterns:
        for match in re.finditer(pat, full_blob, re.IGNORECASE):
            val = match.group(1).strip()
            val = re.sub(r'^[\W_]+|[\W_]+$', '', val)
            if len(val) >= 4 and val.lower() not in [
                "invoice", "number", "date", "statement", "details", "client", "amount", "total", "order", "corp"
            ]:
                data["invoice_number"] = val
                found_number = True
                break
        if found_number:
            break
            
    # Fallback to general patterns if still not found
    if data["invoice_number"] == "NOT FOUND":
        inv_fallbacks = [
            r'\b([A-Z]{3,4}[0-9-]{4,10})\b',
            r'\b([A-Z]+[0-9]+[A-Z0-9-]*)\b',
            r'\b([0-9]{4,10})\b'
        ]
        for pat in inv_fallbacks:
            for match in re.finditer(pat, full_blob):
                val = match.group(1).strip()
                val = re.sub(r'^[\W_]+|[\W_]+$', '', val)
                if val.lower() not in [
                    "invoice", "number", "date", "statement", "details", "client", "amount", "total", "order"
                ]:
                    data["invoice_number"] = val
                    found_number = True
                    break
            if found_number:
                break

    # 3. Invoice Date Scanner
    inv_date_match = re.search(r'\b(?:Invoice\s*Date|Doc\s*Date|Date)\b[^\n\r\d]*?([\d/,\.-]+|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})', full_blob, re.IGNORECASE)
    if inv_date_match:
        invoice_date = inv_date_match.group(1).strip()
        # Clean up Tesseract's slash replacement '1' bug (e.g., 1110212019 -> 11/02/2019)
        if len(invoice_date) == 10 and invoice_date[2] == '1' and invoice_date[5] == '1':
            invoice_date = invoice_date[:2] + '/' + invoice_date[3:5] + '/' + invoice_date[6:]
        data["invoice_date"] = normalize_date(invoice_date)
        
    if data["invoice_date"] in ["NOT FOUND", ""]:
        date_patterns = [
            r'\b(?:Date)[\s\:\.]*([0-9]{1,2}[-/\.][0-9]{1,2}[-/\.][0-9]{2,4})\b',
            r'\b([A-Z][a-z]{2,8}\s+[0-9]{1,2},?\s+[0-9]{4})\b',
            r'\b([0-9]{1,2}[-/\.][0-9]{1,2}[-/\.][0-9]{2,4})\b',
            r'\b([0-9]{4}[-/\.][0-9]{1,2}[-/\.][0-9]{1,2})\b'
        ]
        for pat in date_patterns:
            m = re.search(pat, full_blob, re.IGNORECASE)
            if m:
                prefix = full_blob[max(0, m.start() - 15):m.start()].lower()
                if "due" not in prefix:
                    normalized = normalize_date(m.group(1).strip())
                    if normalized != "NOT FOUND":
                        data["invoice_date"] = normalized
                        break

    # 4. Tax / VAT Amount Scanner
    for line in lines:
        if re.search(r'\b(?:Tax|VAT|TAX|TAK|Tas)\b', line, re.IGNORECASE):
            clean_line = re.sub(r'[\d\.]+\s*%', '', line)
            nums = re.findall(r'[\d,\.]+', clean_line)
            if nums:
                val = nums[-1].replace(',', '')
                val = val.rstrip('.')
                if '.' not in val and len(val) >= 2:
                    if len(val) >= 3:
                        val = val[:-2] + '.' + val[-2:]
                    else:
                        val = "0." + val
                
                try:
                    float(val)
                    data["tax_amount"] = clean_amount_str(val)
                    break
                except:
                    pass

    # 5. Total Amount Summary Line Scanner
    total_match = re.search(r'\b(?:TOTAL|AMOUNT DUE|BALANCE DUE|TOTAL AMOUNT)\b[^\n\r]*?([\d,]+\.\d{2,3})', full_blob, re.IGNORECASE)
    if total_match:
        data["total_amount"] = clean_amount_str(total_match.group(1).strip())
    else:
        # Aggressive Fallback 1: Look for highest dollar values specifically
        dollar_amounts = re.findall(r'\$\s*([\d,]+(?:\.\d+)?)', full_blob)
        if dollar_amounts:
            try:
                floats = [float(a.replace(',', '')) for a in dollar_amounts]
                data["total_amount"] = clean_amount_str(f"{max(floats):.2f}")
            except: pass
        
        # Aggressive Fallback 2: Any largest decimal value
        if data["total_amount"] == "0.00":
            all_amounts = re.findall(r'[\d,]+\.\d{2,3}', full_blob)
            if all_amounts:
                try:
                    floats = [float(a.replace(',', '')) for a in all_amounts]
                    data["total_amount"] = clean_amount_str(f"{max(floats):.2f}")
                except:
                    pass

    return data