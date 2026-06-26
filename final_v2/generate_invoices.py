"""
generate_test_invoices.py
==========================
Generates 30 realistic PDF invoices for an AC (Air Conditioner) parts
inventory system.

Key design decisions matching real-world testing needs:
  - 6 distinct supplier companies (different visual styles, addresses, GSTINs)
  - Same AC parts appear across multiple suppliers (tests deduplication logic)
  - Varied invoice layouts (some minimal, some detailed with letterhead)
  - GST breakdown shown (CGST + SGST or IGST depending on state)
  - Realistic Indian pricing and part numbers
  - Mix of 1-6 line items per invoice

Run:
    pip install reportlab faker
    python generate_test_invoices.py

Output: ./test_invoices/  (30 PDF files)
"""

import os
import random
from pathlib import Path
from datetime import date, timedelta
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

random.seed(99)

OUT_DIR = Path("test_invoices")
OUT_DIR.mkdir(exist_ok=True)

W, H = A4  # 595.27 x 841.89 pts

# ── AC Parts catalog ──────────────────────────────────────────────────────────
# (part_code, description, unit, base_price_INR)
# Same parts appear across multiple suppliers — this is the key test scenario.

AC_PARTS = [
    ("COMP-R22-1.5T",  "Rotary Compressor 1.5 Ton R22",               "Nos",  6800),
    ("COMP-R32-1.5T",  "Inverter Rotary Compressor 1.5 Ton R32",      "Nos",  8400),
    ("COMP-R410-2T",   "Scroll Compressor 2 Ton R410A",               "Nos", 11200),
    ("COND-CU-1.5T",   "Condenser Coil Copper 1.5 Ton",               "Nos",  2200),
    ("COND-AL-2T",     "Condenser Coil Aluminium 2 Ton",              "Nos",  1850),
    ("EVAP-CU-1T",     "Evaporator Coil Copper 1 Ton",                "Nos",  1600),
    ("EVAP-AL-1.5T",   "Evaporator Coil Aluminium 1.5 Ton",          "Nos",  1350),
    ("FAN-ODU-12IN",   "Outdoor Unit Fan Motor 12 inch 25W",          "Nos",   780),
    ("FAN-IDU-9IN",    "Indoor Unit Blower Motor 9 inch 18W",         "Nos",   620),
    ("PCB-CTRL-INV",   "Inverter Control PCB Assembly",               "Nos",  2400),
    ("PCB-DISPLAY",    "Indoor Unit Display PCB",                     "Nos",   850),
    ("PCB-POWER",      "Power Module PCB ODU",                        "Nos",  1200),
    ("CAP-RUN-25MFD",  "Run Capacitor 25 MFD 440V",                   "Nos",   145),
    ("CAP-RUN-30MFD",  "Run Capacitor 30 MFD 440V",                   "Nos",   165),
    ("CAP-START-100MFD","Start Capacitor 100 MFD 250V",               "Nos",   210),
    ("TXV-R22-1.5T",   "Thermostatic Expansion Valve R22 1.5T",       "Nos",   480),
    ("TXV-R32-2T",     "Thermostatic Expansion Valve R32 2T",         "Nos",   620),
    ("FILTER-MESH",    "Air Filter Mesh IDU 300x250mm",               "Nos",    85),
    ("FILTER-HEPA",    "HEPA Filter Panel 400x300mm",                 "Nos",   340),
    ("REMOTE-UNIV",    "Universal Remote Control AC",                  "Nos",   220),
    ("DRAIN-PAN",      "Drain Pan Plastic IDU 1.5 Ton",               "Nos",   380),
    ("DRAIN-HOSE-3M",  "Drain Hose PVC 3 Metre",                      "Mtrs",   95),
    ("COPPER-3/8",     "Copper Pipe 3/8 inch 15 Metre Coil",          "Coil", 1850),
    ("COPPER-1/2",     "Copper Pipe 1/2 inch 15 Metre Coil",          "Coil", 2400),
    ("INSULATION-3/8", "Foam Insulation Sleeve 3/8 inch 1m",         "Mtrs",   38),
    ("VALVE-SVCPORT",  "Service Port Valve Schrader 1/4 SAE",        "Nos",   125),
    ("VALVE-BALL-1/2", "Ball Valve 1/2 inch Brass",                   "Nos",   280),
    ("SENSOR-TEMP",    "NTC Temperature Sensor 10K",                  "Nos",    95),
    ("SENSOR-PRESS",   "High/Low Pressure Switch Dual",               "Nos",   420),
    ("GAS-R22-800G",   "Refrigerant R22 800g Pre-charged Can",        "Nos",   580),
    ("GAS-R32-750G",   "Refrigerant R32 750g Can",                    "Nos",   650),
    ("GASKET-COMP",    "Compressor Mounting Gasket Kit",              "Set",   180),
    ("GROMMET-MTG",    "Rubber Mounting Grommet Set (4 pcs)",         "Set",    95),
    ("THERMOSTAT-1T",  "Bimetallic Thermostat 1 Ton",                 "Nos",   320),
    ("RELAY-COMP",     "Compressor Overload Relay 25A",               "Nos",   185),
    ("CONTACTOR-25A",  "AC Contactor 25A 230V Coil",                  "Nos",   265),
    ("CAPACITOR-DUAL", "Dual Run Capacitor 35+5 MFD 440V",            "Nos",   210),
    ("MOTOR-COND-FAN", "Condenser Fan Motor 1/6 HP 1075 RPM",         "Nos",   920),
    ("BELT-AHU",       "AHU Belt B-Section B54",                      "Nos",   145),
    ("BEARING-6205",   "Deep Groove Ball Bearing 6205 ZZ",            "Nos",   185),
]

# ── Suppliers ─────────────────────────────────────────────────────────────────
# 6 suppliers, each with unique styling and address.

SUPPLIERS = [
    {
        "name":    "Arjun Refrigeration Parts Pvt Ltd",
        "addr1":   "Plot 14, MIDC Industrial Area",
        "addr2":   "Bhosari, Pune - 411026, Maharashtra",
        "gstin":   "27AARCA1234B1Z5",
        "pan":     "AARCA1234B",
        "phone":   "+91 20 2747 8800",
        "email":   "sales@arjunrefrig.com",
        "bank":    "HDFC Bank | A/c: 50200034567890 | IFSC: HDFC0001234",
        "state":   "MH",  # same state → CGST + SGST
        "style":   "blue",
    },
    {
        "name":    "Sheetala Cooling Components",
        "addr1":   "123 Refrigeration Market, Near Andheri Station",
        "addr2":   "Andheri West, Mumbai - 400053, Maharashtra",
        "gstin":   "27AASCS5678C1Z2",
        "pan":     "AASCS5678C",
        "phone":   "+91 22 6654 9900",
        "email":   "info@sheetalacooling.in",
        "bank":    "ICICI Bank | A/c: 123456789012 | IFSC: ICIC0001234",
        "state":   "MH",
        "style":   "green",
    },
    {
        "name":    "Polar HVAC Supplies LLP",
        "addr1":   "Unit 7, Sector 63 Industrial Estate",
        "addr2":   "Noida, Uttar Pradesh - 201301",
        "gstin":   "09AAJFP9012D1Z8",
        "pan":     "AAJFP9012D",
        "phone":   "+91 120 456 7890",
        "email":   "polar.hvac@gmail.com",
        "bank":    "SBI | A/c: 32109876543 | IFSC: SBIN0007890",
        "state":   "UP",  # different state → IGST
        "style":   "dark",
    },
    {
        "name":    "Sai Krishna AC Parts & Service",
        "addr1":   "8-2-293 Road No. 3, Banjara Hills",
        "addr2":   "Hyderabad, Telangana - 500034",
        "gstin":   "36AAPFS3456E1Z1",
        "pan":     "AAPFS3456E",
        "phone":   "+91 40 2354 7600",
        "email":   "saikrishna.ac@yahoo.com",
        "bank":    "Axis Bank | A/c: 921010012345678 | IFSC: UTIB0001234",
        "state":   "TS",  # different state → IGST
        "style":   "minimal",
    },
    {
        "name":    "Snowflake Enterprises",
        "addr1":   "32 Air Conditioning Market, Ellis Bridge",
        "addr2":   "Ahmedabad, Gujarat - 380006",
        "gstin":   "24AAPSE7890F1Z4",
        "pan":     "AAPSE7890F",
        "phone":   "+91 79 2657 3300",
        "email":   "snowflake.ent@gmail.com",
        "bank":    "Kotak Mahindra | A/c: 1234567890 | IFSC: KKBK0001234",
        "state":   "GJ",
        "style":   "orange",
    },
    {
        "name":    "Techno Cool Components Pvt Ltd",
        "addr1":   "Plot 88, Electronic City Phase 2",
        "addr2":   "Bengaluru, Karnataka - 560100",
        "gstin":   "29AATCT1234G1Z7",
        "pan":     "AATCT1234G",
        "phone":   "+91 80 4123 5600",
        "email":   "technocool@technocool.com",
        "bank":    "Yes Bank | A/c: 012345678901234 | IFSC: YESB0001234",
        "state":   "KA",
        "style":   "teal",
    },
]

BUYER = {
    "name":  "Frost Air Systems Pvt Ltd",
    "addr1": "Survey No. 245, Chakan Industrial Area",
    "addr2": "Chakan, Pune - 410501, Maharashtra",
    "gstin": "27AABCF9876H1Z3",
    "state": "MH",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def rand_date(start="2024-01-01", end="2025-03-31"):
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    return s + timedelta(days=random.randint(0, (e - s).days))


def inv_no(supplier_idx, n):
    prefixes = ["ARP", "SCC", "PHV", "SKA", "SNF", "TCC"]
    return f"{prefixes[supplier_idx]}/{2024 + n//20}/{n:04d}"


def gst_type(supplier):
    """Same state as buyer (MH) → CGST+SGST, else IGST."""
    return "intra" if supplier["state"] == BUYER["state"] else "inter"


def make_line_items(n=None):
    n = n or random.randint(1, 6)
    chosen = random.sample(AC_PARTS, min(n, len(AC_PARTS)))
    items = []
    for code, desc, unit, base in chosen:
        qty = random.choice([1, 1, 2, 2, 3, 4, 5, 10, 12])
        price = round(base * random.uniform(0.90, 1.12), 2)
        amount = round(qty * price, 2)
        items.append({
            "code": code, "desc": desc, "unit": unit,
            "qty": qty, "price": price, "amount": amount
        })
    return items


# ── Style colours ─────────────────────────────────────────────────────────────

STYLE_COLORS = {
    "blue":    (colors.HexColor("#1e40af"), colors.HexColor("#dbeafe"), colors.HexColor("#1e3a8a")),
    "green":   (colors.HexColor("#15803d"), colors.HexColor("#dcfce7"), colors.HexColor("#14532d")),
    "dark":    (colors.HexColor("#0f172a"), colors.HexColor("#f1f5f9"), colors.HexColor("#1e293b")),
    "minimal": (colors.HexColor("#374151"), colors.HexColor("#f9fafb"), colors.HexColor("#111827")),
    "orange":  (colors.HexColor("#c2410c"), colors.HexColor("#fff7ed"), colors.HexColor("#7c2d12")),
    "teal":    (colors.HexColor("#0f766e"), colors.HexColor("#f0fdfa"), colors.HexColor("#134e4a")),
}


# ── Renderer ──────────────────────────────────────────────────────────────────

def render_invoice(supplier, items, inv_num, inv_date, out_path):
    accent, light, dark = STYLE_COLORS[supplier["style"]]
    gst_mode = gst_type(supplier)

    cv = canvas.Canvas(str(out_path), pagesize=A4)

    # ── Top accent bar ──
    cv.setFillColor(accent)
    cv.rect(0, H - 12*mm, W, 12*mm, fill=1, stroke=0)

    # ── Supplier name ──
    cv.setFillColor(colors.white)
    cv.setFont("Helvetica-Bold", 14)
    cv.drawString(15*mm, H - 8.5*mm, supplier["name"].upper())

    # ── TAX INVOICE label ──
    cv.setFont("Helvetica-Bold", 11)
    cv.drawRightString(W - 15*mm, H - 8.5*mm, "TAX INVOICE")

    y = H - 22*mm

    # ── Supplier details block (left) ──
    cv.setFillColor(dark)
    cv.setFont("Helvetica", 8)
    for line in [supplier["addr1"], supplier["addr2"],
                 f"GSTIN: {supplier['gstin']}  |  PAN: {supplier['pan']}",
                 f"Ph: {supplier['phone']}  |  {supplier['email']}"]:
        cv.drawString(15*mm, y, line)
        y -= 5*mm

    # ── Invoice meta (right) ──
    cv.setFont("Helvetica-Bold", 9)
    cv.drawRightString(W - 15*mm, H - 22*mm, f"Invoice No: {inv_num}")
    cv.setFont("Helvetica", 9)
    cv.drawRightString(W - 15*mm, H - 28*mm, f"Date: {inv_date.strftime('%d/%m/%Y')}")
    due = inv_date + timedelta(days=30)
    cv.drawRightString(W - 15*mm, H - 34*mm, f"Due Date: {due.strftime('%d/%m/%Y')}")
    cv.drawRightString(W - 15*mm, H - 40*mm, "Payment Terms: Net 30 Days")

    # ── Divider ──
    y = H - 50*mm
    cv.setStrokeColor(accent)
    cv.setLineWidth(1)
    cv.line(15*mm, y, W - 15*mm, y)
    y -= 6*mm

    # ── Bill To ──
    cv.setFillColor(accent)
    cv.setFont("Helvetica-Bold", 8)
    cv.drawString(15*mm, y, "BILL TO:")
    y -= 5*mm
    cv.setFillColor(dark)
    cv.setFont("Helvetica-Bold", 10)
    cv.drawString(15*mm, y, BUYER["name"])
    y -= 5*mm
    cv.setFont("Helvetica", 8)
    cv.drawString(15*mm, y, BUYER["addr1"])
    y -= 5*mm
    cv.drawString(15*mm, y, BUYER["addr2"])
    y -= 5*mm
    cv.drawString(15*mm, y, f"GSTIN: {BUYER['gstin']}")
    y -= 8*mm

    # ── Line items table ──
    subtotal = sum(i["amount"] for i in items)
    gst_rate = 0.18  # 18% GST on AC parts (standard rate)
    gst_amt = round(subtotal * gst_rate, 2)
    total = round(subtotal + gst_amt, 2)

    tw = W - 30*mm

    # Header
    if gst_mode == "intra":
        tax_cols = ["CGST 9%", "SGST 9%"]
    else:
        tax_cols = ["IGST 18%", ""]

    hdr = ["SR", "PART CODE", "DESCRIPTION", "QTY", "UNIT", "RATE (INR)", "AMOUNT (INR)"]
    col_w = [8*mm, 28*mm, tw*0.38, 12*mm, 12*mm, 28*mm, 28*mm]

    # Adjust col_w to fill exactly tw
    total_w = sum(col_w)
    col_w[2] += tw - total_w

    data = [hdr]
    for i, item in enumerate(items, 1):
        data.append([
            str(i),
            item["code"],
            item["desc"],
            str(item["qty"]),
            item["unit"],
            f"{item['price']:,.2f}",
            f"{item['amount']:,.2f}",
        ])

    t = Table(data, colWidths=col_w)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), accent),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("ALIGN",         (2, 0), (2, -1), "LEFT"),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, light]),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
    ]))

    table_h = (len(items) + 1) * 12*mm
    t.wrapOn(cv, tw, H)
    t.drawOn(cv, 15*mm, y - table_h)
    y = y - table_h - 6*mm

    # ── Totals block ──
    tx = W - 15*mm
    ty = y

    def tot_line(label, val, bold=False):
        nonlocal ty
        cv.setFont("Helvetica-Bold" if bold else "Helvetica", 8.5)
        cv.setFillColor(dark)
        cv.drawRightString(tx - 38*mm, ty, label)
        cv.drawRightString(tx, ty, val)
        ty -= 6*mm

    cv.setStrokeColor(colors.HexColor("#d1d5db"))
    cv.setLineWidth(0.5)
    cv.line(W//2, ty + 3*mm, tx, ty + 3*mm)

    tot_line("Subtotal:", f"INR {subtotal:,.2f}")

    if gst_mode == "intra":
        tot_line(f"CGST @ 9%:", f"INR {gst_amt/2:,.2f}")
        tot_line(f"SGST @ 9%:", f"INR {gst_amt/2:,.2f}")
    else:
        tot_line(f"IGST @ 18%:", f"INR {gst_amt:,.2f}")

    cv.setFillColor(accent)
    cv.rect(W//2, ty - 1*mm, tx - W//2, 8*mm, fill=1, stroke=0)
    cv.setFillColor(colors.white)
    cv.setFont("Helvetica-Bold", 9)
    cv.drawRightString(tx - 2*mm, ty + 1.5*mm, f"TOTAL DUE:  INR {total:,.2f}")
    ty -= 9*mm

    # ── Bank details ──
    cv.setFillColor(colors.HexColor("#6b7280"))
    cv.setFont("Helvetica", 7.5)
    cv.drawString(15*mm, ty, f"Bank Details: {supplier['bank']}")
    ty -= 5*mm
    cv.drawString(15*mm, ty,
        "This is a computer generated invoice. Subject to jurisdiction of courts in "
        + supplier["addr2"].split(",")[-1].strip().split("-")[0].strip() + ".")

    # ── Bottom bar ──
    cv.setFillColor(accent)
    cv.rect(0, 0, W, 5*mm, fill=1, stroke=0)

    cv.save()


# ── Generate 30 invoices ─────────────────────────────────────────────────────

def main():
    generated = []
    inv_counter = 1

    # Strategy: 5 invoices per supplier, but items overlap across suppliers
    for sup_idx, supplier in enumerate(SUPPLIERS):
        for inv_in_sup in range(5):
            # Pick 2-5 items; ensure at least one item appears in another supplier's invoice
            n_items = random.randint(2, 5)
            items = make_line_items(n_items)
            inv_date = rand_date()
            inv_num = inv_no(sup_idx, inv_counter)
            fname = f"INV_{inv_counter:02d}_{supplier['name'].split()[0]}_{inv_date.strftime('%Y%m%d')}.pdf"
            out_path = OUT_DIR / fname
            render_invoice(supplier, items, inv_num, inv_date, out_path)
            total = sum(i["amount"] for i in items)
            gst = round(total * 0.18, 2)
            print(f"[{inv_counter:2d}/30] {fname}")
            print(f"       Supplier : {supplier['name']}")
            print(f"       Items    : {', '.join(i['code'] for i in items)}")
            print(f"       Total    : INR {total + gst:,.2f}  ({gst_type(supplier).upper()} GST)")
            generated.append(fname)
            inv_counter += 1

    print(f"\n✓ Generated {len(generated)} invoices in ./{OUT_DIR}/")
    print("\nTo test your extraction pipeline:")
    print("  cd test_invoices")
    print("  python ../extraction_engine.py INV_01_*.pdf  # or drop files in the web UI")
    print("\nSame AC parts appear across multiple suppliers to test:")
    print("  - Cross-supplier item deduplication in product master")
    print("  - Supplier linkage on return inspections")
    print("  - Stock aggregation across multiple GRNs")


if __name__ == "__main__":
    main()