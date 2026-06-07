import re
from extractor import extract_text_from_file

files = [
    r'uploads\Screenshot 2026-05-23 110318.png', 
    r'uploads\Screenshot 2026-05-23 120400.png', 
    r'uploads\Screenshot 2026-05-23 120452.png'
]

for f in files:
    print('==', f, '==')
    txt = extract_text_from_file(f)
    clean_text = re.sub(r' +', ' ', txt)
    lines = [line.strip() for line in clean_text.split('\n') if line.strip()]
    full = '\n'.join(lines)
    
    # Vendor strategy
    vendor = "UNKNOWN VENDOR"
    for line in lines:
        lower = line.lower()
        if any(k in lower for k in ['invoice', 'order', 'bill to', 'ship to', 'date', 'qty', 'to :', 'from:', 'to:', '[', ']', 'regn', 'tax', 'total', 'due']):
            continue
        if len(line) > 3 and not re.search(r'^\d+$', line.strip()) and not '---' in line:
            vendor = line
            break
    print(' VENDOR:', vendor)
    
    # Date strategy
    date_patterns = [
        r'(?:Date)[\s\:]*([0-9]{1,2}[-/\.][0-9]{1,2}[-/\.][0-9]{2,4})',
        r'([A-Z][a-z]{2,8}\s[0-9]{1,2},?\s[0-9]{4})',
        r'([0-9]{1,2}[-/\.][0-9]{1,2}[-/\.][0-9]{2,4})'
    ]
    extracted_date = "NOT FOUND"
    for pat in date_patterns:
        m = re.search(pat, full, re.IGNORECASE)
        if m:
            extracted_date = m.group(1).strip()
            break
    print(' DATE:', extracted_date)
    
    # Invoice number strategy
    inv_num = "NOT FOUND"
    inv_match = re.search(r'(?:Invoice|Order|Ref|#)\s*(?:No|Num|Number)?[\s\:\#]*([A-Za-z0-9-]{4,})', full, re.IGNORECASE)
    if inv_match and "date" not in inv_match.group(0).lower():
        inv_num = inv_match.group(1).strip()
    else:
        inv_fallback = re.search(r'\b([A-Z0-9-]{5,})\b', full)
        if inv_fallback:
            inv_num = inv_fallback.group(1).strip()
    print(' INV_NUM:', inv_num)

