import os
from PIL import Image, ImageDraw, ImageFont

def create_invoice_1():
    """Generates Invoice 1: Tech Solutions Inc. (Clean Corporate Layout)"""
    img = Image.new('RGB', (800, 1000), color='white')
    draw = ImageDraw.Draw(img)
    
    # Load Windows built-in Arial font
    try:
        font_large = ImageFont.truetype("arial.ttf", 28)
        font_med = ImageFont.truetype("arial.ttf", 16)
        font_bold = ImageFont.truetype("arial.ttf", 14)
        font_small = ImageFont.truetype("arial.ttf", 12)
    except IOError:
        # Fallback to default bitmap font if Arial is not found
        font_large = font_med = font_bold = font_small = ImageFont.load_default()

    # Vendor Header
    draw.text((50, 50), "TECH SOLUTIONS INC.", fill="black", font=font_large)
    draw.text((50, 90), "123 Cloud Avenue, Seattle, WA 98101", fill="gray", font=font_small)
    draw.text((50, 110), "Email: billing@techsolutions.com | Web: www.techsolutions.com", fill="gray", font=font_small)

    # Invoice Details (Right-aligned look)
    draw.text((500, 50), "INVOICE", fill="black", font=font_large)
    draw.text((500, 90), "Invoice Number: INV-2026-0042", fill="black", font=font_bold)
    draw.text((500, 110), "Invoice Date: May 12, 2026", fill="black", font=font_bold)
    draw.text((500, 130), "Due Date: June 12, 2026", fill="gray", font=font_small)

    # Bill To
    draw.text((50, 180), "BILL TO:", fill="black", font=font_bold)
    draw.text((50, 200), "Acme Corporation", fill="black", font=font_med)
    draw.text((50, 220), "456 Enterprise Way, Suite 100", fill="gray", font=font_small)
    draw.text((50, 240), "San Jose, CA 95110", fill="gray", font=font_small)

    # Table Header Line
    draw.line((50, 300, 750, 300), fill="black", width=2)
    draw.text((55, 310), "Description", fill="black", font=font_bold)
    draw.text((450, 310), "Qty", fill="black", font=font_bold)
    draw.text((550, 310), "Unit Price", fill="black", font=font_bold)
    draw.text((650, 310), "Total", fill="black", font=font_bold)
    draw.line((50, 335, 750, 335), fill="gray", width=1)

    # Line Items
    draw.text((55, 350), "Cloud Infrastructure Hosting Services (Monthly)", fill="black", font=font_small)
    draw.text((450, 350), "1", fill="black", font=font_small)
    draw.text((550, 350), "$350.00", fill="black", font=font_small)
    draw.text((650, 350), "$350.00", fill="black", font=font_small)

    draw.text((55, 380), "Advanced IT Support & Security Maintenance Bundle", fill="black", font=font_small)
    draw.text((450, 380), "1", fill="black", font=font_small)
    draw.text((550, 380), "$150.00", fill="black", font=font_small)
    draw.text((650, 380), "$150.00", fill="black", font=font_small)

    # Totals Section
    draw.line((50, 500, 750, 500), fill="gray", width=1)
    draw.text((500, 520), "Subtotal:", fill="black", font=font_small)
    draw.text((650, 520), "$500.00", fill="black", font=font_small)
    
    draw.text((500, 545), "Tax (9%):", fill="black", font=font_bold)
    draw.text((650, 545), "$45.00", fill="black", font=font_bold)

    draw.line((500, 575, 750, 575), fill="gray", width=1)
    draw.text((500, 590), "TOTAL AMOUNT:", fill="black", font=font_bold)
    draw.text((650, 590), "$545.00", fill="black", font=font_large)
    draw.line((500, 630, 750, 630), fill="black", width=2)

    # Footer
    draw.text((50, 900), "Thank you for your business!", fill="gray", font=font_bold)
    draw.text((50, 920), "Payment is due within 30 days of the invoice date.", fill="gray", font=font_small)
    
    return img

def create_invoice_2():
    """Generates Invoice 2: Global Logistics Co. (Tabular Grid Layout)"""
    img = Image.new('RGB', (800, 1000), color='white')
    draw = ImageDraw.Draw(img)
    
    try:
        font_large = ImageFont.truetype("arial.ttf", 28)
        font_med = ImageFont.truetype("arial.ttf", 16)
        font_bold = ImageFont.truetype("arial.ttf", 14)
        font_small = ImageFont.truetype("arial.ttf", 12)
    except IOError:
        font_large = font_med = font_bold = font_small = ImageFont.load_default()

    # Vendor Header
    draw.text((50, 50), "GLOBAL LOGISTICS CO.", fill="black", font=font_large)
    draw.text((50, 90), "88 Shipping Lane, New York, NY 10001", fill="black", font=font_small)
    
    # Invoice details
    draw.text((500, 50), "INVOICE DETAILS", fill="black", font=font_med)
    draw.text((500, 75), "Invoice No: GLB987654", fill="black", font=font_bold)
    draw.text((500, 95), "Date: 25/05/2026", fill="black", font=font_bold)
    draw.text((500, 115), "PO Number: PO-99238", fill="gray", font=font_small)

    # Client
    draw.text((50, 180), "SHIP & BILL TO:", fill="black", font=font_bold)
    draw.text((50, 200), "SuperMart Enterprises", fill="black", font=font_med)
    draw.text((50, 220), "999 Retail Blvd, Warehouse D", fill="gray", font=font_small)

    # Grid Header
    draw.rectangle([50, 280, 750, 310], fill="#f2f2f2")
    draw.text((60, 290), "Item / Description", fill="black", font=font_bold)
    draw.text((400, 290), "Weight", fill="black", font=font_bold)
    draw.text((500, 290), "Rate", fill="black", font=font_bold)
    draw.text((650, 290), "Amount", fill="black", font=font_bold)

    # Line Item 1
    draw.text((60, 330), "Ocean Freight - Standard Container Shipping", fill="black", font=font_small)
    draw.text((400, 330), "12,500 kg", fill="black", font=font_small)
    draw.text((500, 330), "$1.20 / kg", fill="black", font=font_small)
    draw.text((650, 330), "$1500.00", fill="black", font=font_small)
    
    # Line Item 2
    draw.text((60, 360), "Customs Clearance & Documentation Processing Fee", fill="black", font=font_small)
    draw.text((400, 360), "N/A", fill="black", font=font_small)
    draw.text((500, 360), "Flat Rate", fill="black", font=font_small)
    draw.text((650, 360), "$120.50", fill="black", font=font_small)

    # Summary box
    draw.line((50, 420, 750, 420), fill="gray", width=1)
    
    # Calculations
    draw.text((480, 440), "Subtotal:", fill="black", font=font_small)
    draw.text((650, 440), "$1500.00", fill="black", font=font_small)
    
    draw.text((480, 470), "Tax / VAT Details:", fill="black", font=font_bold)
    draw.text((650, 470), "$120.50", fill="black", font=font_bold)

    draw.rectangle([480, 500, 750, 540], fill="#e6f4ea")
    draw.text((490, 512), "TOTAL AMOUNT:", fill="black", font=font_bold)
    draw.text((640, 512), "$1,620.50", fill="black", font=font_bold)

    # Bank Details
    draw.text((50, 600), "BANK WIRE PAYMENT DETAILS:", fill="black", font=font_bold)
    draw.text((50, 620), "Bank Name: National Commerce Bank", fill="gray", font=font_small)
    draw.text((50, 640), "Routing / SWIFT: NCBUA22XXX", fill="gray", font=font_small)
    draw.text((50, 660), "Account Number: 120039948210", fill="gray", font=font_small)

    return img

def create_invoice_3():
    """Generates Invoice 3: Apex Marketing Group (Modern Minimalist Layout)"""
    img = Image.new('RGB', (800, 1000), color='white')
    draw = ImageDraw.Draw(img)
    
    try:
        font_large = ImageFont.truetype("arial.ttf", 28)
        font_med = ImageFont.truetype("arial.ttf", 16)
        font_bold = ImageFont.truetype("arial.ttf", 14)
        font_small = ImageFont.truetype("arial.ttf", 12)
    except IOError:
        font_large = font_med = font_bold = font_small = ImageFont.load_default()

    # Left Accent Bar
    draw.rectangle([0, 0, 15, 1000], fill="#6366f1")

    # Vendor Details
    draw.text((50, 50), "APEX MARKETING GROUP", fill="black", font=font_large)
    draw.text((50, 90), "45 Creative Way, San Francisco, CA 94107", fill="gray", font=font_small)
    
    # Metadata
    draw.text((500, 50), "INVOICE STATEMENT", fill="gray", font=font_med)
    draw.text((500, 75), "Ref ID: AMP-9921", fill="black", font=font_bold)
    draw.text((500, 95), "Date: 2026-06-01", fill="black", font=font_bold)

    # Customer
    draw.text((50, 170), "CLIENT REGISTRY:", fill="gray", font=font_bold)
    draw.text((50, 190), "Tech Ventures Corp", fill="black", font=font_med)
    draw.text((50, 210), "Suite 500, Innovator Tower, Boston, MA", fill="gray", font=font_small)

    # Line Item
    draw.line((50, 280, 750, 280), fill="gray", width=1)
    draw.text((55, 300), "Professional SEO Campaign & Social Media Ad Consulting", fill="black", font=font_med)
    draw.text((55, 325), "Service Period: June 2026 - Digital Marketing Strategy", fill="gray", font=font_small)
    draw.text((650, 300), "$300.00", fill="black", font=font_bold)
    draw.line((50, 355, 750, 355), fill="gray", width=1)

    # Tax details
    draw.text((450, 380), "Tax / VAT (7.00%):", fill="black", font=font_small)
    draw.text((650, 380), "$21.00", fill="black", font=font_small)

    # Total Due
    draw.text((450, 410), "TOTAL AMOUNT DUE:", fill="black", font=font_bold)
    draw.text((650, 410), "$321.00", fill="black", font=font_bold)
    draw.line((450, 435, 750, 435), fill="black", width=2)

    return img

if __name__ == '__main__':
    # Ensure standard directory setups
    os.makedirs('test_invoices', exist_ok=True)
    os.makedirs('uploads', exist_ok=True)
    
    print("Generating crisp mock invoices...")
    
    img1 = create_invoice_1()
    img1.save(os.path.join('test_invoices', 'invoice_tech_solutions.png'))
    img1.save(os.path.join('uploads', 'invoice_tech_solutions.png'))
    print("Saved Invoice 1 (Tech Solutions Inc.)")
    
    img2 = create_invoice_2()
    img2.save(os.path.join('test_invoices', 'invoice_global_logistics.png'))
    img2.save(os.path.join('uploads', 'invoice_global_logistics.png'))
    print("Saved Invoice 2 (Global Logistics Co.)")
    
    img3 = create_invoice_3()
    img3.save(os.path.join('test_invoices', 'invoice_apex_marketing.png'))
    img3.save(os.path.join('uploads', 'invoice_apex_marketing.png'))
    print("Saved Invoice 3 (Apex Marketing Group)")
    
    print("Mock invoice generation successfully completed!")
