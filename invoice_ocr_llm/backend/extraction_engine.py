"""
Invoice Extraction Engine
==========================
Pipeline: PDF/Image -> Text (OCR or direct PDF text) -> Local LLM (Ollama) -> JSON

Fully open source, fully local, no API keys, no internet required after setup.

Dependencies:
    pip install pdfplumber pdf2image pillow pytesseract requests

System dependencies:
    poppler-utils (for pdf2image)
    tesseract-ocr  (e.g. `brew install tesseract` on Mac, `apt install tesseract-ocr` on Linux)
    Ollama installed and running (https://ollama.com)
    A model pulled, e.g.: ollama pull qwen2.5:3b
"""

import io
import json
import re
from pathlib import Path
from typing import Optional

import requests
from PIL import Image


# ── Configuration ──────────────────────────────────────────────────────────────

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:3b"   # change to whatever model you've pulled

# Column gap threshold (in pixels) used when reconstructing table columns
# from OCR bounding boxes. If columns are merging incorrectly, increase this.
# If columns are splitting too aggressively, decrease this.
OCR_COLUMN_GAP_THRESHOLD = 40

# Minimum characters of extracted text below which we consider a PDF "scanned"
# and fall back to OCR instead of using pdfplumber's direct text extraction.
DIGITAL_TEXT_THRESHOLD = 50


# ── Step 1: Get text out of the file ───────────────────────────────────────────

class TextExtractor:
    """Extracts raw text from PDFs (digital or scanned) and images."""

    def __init__(self):
        self._ocr = None  # lazy-loaded

    @property
    def ocr(self):
        if self._ocr is None:
            try:
                import pytesseract
            except ModuleNotFoundError as e:
                raise RuntimeError(
                    "pytesseract is required for OCR but is not installed. "
                    "Install it with `pip install pytesseract` and "
                    "`brew install tesseract` (Mac) or `apt install tesseract-ocr` (Linux)."
                ) from e

            # Verify the tesseract binary itself is on PATH, not just the Python wrapper
            try:
                pytesseract.get_tesseract_version()
            except Exception as e:
                raise RuntimeError(
                    "pytesseract is installed but the tesseract binary was not found. "
                    "Install it with `brew install tesseract` (Mac) or "
                    "`apt install tesseract-ocr` (Linux), then restart the server."
                ) from e

            self._ocr = pytesseract
        return self._ocr

    def extract(self, file_bytes: bytes, filename: str) -> dict:
        """
        Returns:
            {
              "text": "raw text content",
              "method": "pdf_text" | "ocr",
              "pages": int
            }
        """
        name_lower = filename.lower()

        if name_lower.endswith(".pdf"):
            return self._extract_from_pdf(file_bytes)
        else:
            # Plain image file -> always OCR
            image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            text = self._ocr_image(image)
            return {"text": text, "method": "ocr", "pages": 1}

    def _extract_from_pdf(self, file_bytes: bytes) -> dict:
        import pdfplumber

        # First try: direct text extraction (works for digital/generated PDFs)
        all_text = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            n_pages = len(pdf.pages)
            for page in pdf.pages:
                page_text = page.extract_text() or ""

                # Try to extract any tables on this page separately.
                # Tables preserve column boundaries, which prevents numbers
                # inside item descriptions (e.g. "Steel Coil HR 2mm") from
                # being confused with quantity/price columns.
                table_text = self._format_tables(page)

                all_text.append(page_text + table_text)

        combined = "\n".join(all_text).strip()

        if len(combined) >= DIGITAL_TEXT_THRESHOLD:
            return {"text": combined, "method": "pdf_text", "pages": n_pages}

        # Fallback: scanned PDF -> convert to images and OCR each page
        from pdf2image import convert_from_bytes
        images = convert_from_bytes(file_bytes, dpi=200)

        ocr_texts = []
        for img in images:
            ocr_texts.append(self._ocr_image(img))

        return {"text": "\n\n--- PAGE BREAK ---\n\n".join(ocr_texts),
                "method": "ocr", "pages": len(images)}

    @staticmethod
    def _format_tables(page) -> str:
        """
        Extract tables from a pdfplumber page and format each row with
        ' | ' separators so column boundaries survive into the LLM prompt.
        """
        try:
            tables = page.extract_tables()
        except Exception:
            return ""

        if not tables:
            return ""

        table_text = ""
        for table in tables:
            if not table:
                continue
            table_text += "\n\n[TABLE]\n"
            for row in table:
                cells = [str(c).strip().replace("\n", " ") if c else "" for c in row]
                # Skip fully empty rows
                if any(cells):
                    table_text += " | ".join(cells) + "\n"
            table_text += "[/TABLE]\n"

        return table_text

    def _ocr_image(self, image: Image.Image) -> str:
        """
        Run Tesseract OCR on a PIL image, return text in reading order.

        For rows that look like table rows (3+ words), attempt to reconstruct
        column boundaries from x-positions and join cells with ' | ' so the
        LLM can tell apart e.g. "Steel Coil HR 2mm" (description) from
        "10" (quantity) even though both contain numbers.
        """
        data = self.ocr.image_to_data(image, output_type=self.ocr.Output.DICT)

        # Each word: (y_top, x_left, text), skipping empty detections
        detections = []
        for i in range(len(data["text"])):
            text = data["text"][i].strip()
            if not text:
                continue
            x = data["left"][i]
            y = data["top"][i]
            detections.append((y, x, text))

        if not detections:
            return ""

        # Group into rows by y-proximity
        detections.sort(key=lambda d: d[0])
        rows = []
        current_row = []
        last_y = None
        row_threshold = 10  # pixels — Tesseract's word boxes are tighter than PaddleOCR's

        for y, x, text in detections:
            if last_y is None or abs(y - last_y) < row_threshold:
                current_row.append((x, text))
            else:
                rows.append(current_row)
                current_row = [(x, text)]
            last_y = y
        if current_row:
            rows.append(current_row)

        # Sort each row left-to-right
        for row in rows:
            row.sort(key=lambda r: r[0])

        # Find column boundaries using rows that look like table rows
        # (3+ words is a decent signal for "description | qty | price | amount"-style rows)
        multi_word_rows = [r for r in rows if len(r) >= 3]
        column_starts = None

        if len(multi_word_rows) >= 2:
            all_x = sorted(x for row in multi_word_rows for x, _ in row)
            column_starts = [all_x[0]]
            for prev, curr in zip(all_x, all_x[1:]):
                if curr - prev > OCR_COLUMN_GAP_THRESHOLD:
                    column_starts.append(curr)

        lines = []
        for row in rows:
            if column_starts and len(row) >= 3:
                lines.append(self._row_to_columns(row, column_starts))
            else:
                lines.append(" ".join(text for _, text in row))

        return "\n".join(lines)

    @staticmethod
    def _row_to_columns(row, column_starts) -> str:
        """Assign each word in a row to its nearest column, join with ' | '."""
        cols = [""] * len(column_starts)
        for x, text in row:
            col_idx = min(range(len(column_starts)),
                           key=lambda i: abs(column_starts[i] - x))
            cols[col_idx] = (cols[col_idx] + " " + text).strip()
        return " | ".join(c for c in cols if c)


# ── Step 2: Send text to local LLM for structured extraction ───────────────────

EXTRACTION_PROMPT_TEMPLATE = """You are an invoice data extraction system. Below is text extracted from an invoice using OCR. The text may have minor OCR errors, jumbled spacing, or out-of-order lines.

Extract the following fields and return ONLY a valid JSON object, with no explanation, no markdown formatting, no code fences. If a field cannot be found, use null.

Required JSON structure:
{{
  "vendor_name": string or null,
  "invoice_number": string or null,
  "invoice_date": string or null (format as found in the document),
  "due_date": string or null,
  "currency": string or null (e.g. "INR", "USD"),
  "subtotal": number or null,
  "tax_amount": number or null,
  "total_amount": number or null,
  "buyer_name": string or null,
  "line_items": [
    {{
      "description": string,
      "quantity": number or null,
      "unit_price": number or null,
      "amount": number or null
    }}
  ]
}}

Rules:
- total_amount should be the final amount due, typically the largest amount near words like "Total", "Grand Total", "Amount Due"
- Numbers must be plain numbers without currency symbols or commas (e.g. 48200.00 not "INR 48,200.00")
- If line items are unclear, return an empty array for line_items rather than guessing
- Text between [TABLE] and [/TABLE] markers, or lines containing " | " separators,
  represent table rows where each segment between separators is a separate
  column (typically: description | quantity | unit price | amount). Use this
  structure to correctly separate item descriptions from their numeric values,
  even when the description itself contains numbers (e.g. "Steel Coil HR 2mm",
  "Pipe 3/4 inch"). Do not mistake numbers inside a description column for the
  quantity, unit price, or amount columns.
- The vendor (seller) is typically the company whose name and address appear
  at the very top of the document, often in the largest font or in a header
  block. GSTIN/PAN numbers near the top of the document usually belong to the
  vendor.
- The buyer is typically introduced by labels like "Bill To", "Customer",
  "Ship To", "Buyer", or appears in a distinct block separate from the header.
  GSTIN/PAN numbers near a "Bill To" section usually belong to the buyer.
- Return ONLY the JSON object, nothing else

OCR TEXT:
---
{ocr_text}
---

JSON output:"""


class LLMExtractor:
    """Sends OCR text to a local Ollama model and parses the JSON response."""

    def __init__(self, model: str = OLLAMA_MODEL, ollama_url: str = OLLAMA_URL):
        self.model = model
        self.ollama_url = ollama_url

    def check_connection(self) -> tuple[bool, str]:
        """Check if Ollama is running and the model is available."""
        try:
            r = requests.get(self.ollama_url.replace("/api/generate", "/api/tags"), timeout=5)
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            if any(self.model in m for m in models):
                return True, f"Connected. Model '{self.model}' available."
            return False, f"Ollama running, but model '{self.model}' not found. Pulled models: {models}"
        except requests.exceptions.ConnectionError:
            return False, "Cannot connect to Ollama. Is it running? Try: ollama serve"
        except Exception as e:
            return False, f"Error checking Ollama: {e}"

    def extract(self, ocr_text: str) -> tuple[dict, str]:
        """
        Returns (parsed_json_dict, raw_llm_response)
        Raises RuntimeError if Ollama call fails or JSON cannot be parsed.
        """
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(ocr_text=ocr_text[:6000])  # cap context

        response = requests.post(
            self.ollama_url,
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "format": "json",   # Ollama's JSON mode - forces valid JSON output
                "options": {
                    "temperature": 0.1,  # low temperature for consistent extraction
                }
            },
            timeout=300,
        )
        response.raise_for_status()

        raw_output = response.json().get("response", "")
        parsed = self._parse_json(raw_output)
        return parsed, raw_output

    def _parse_json(self, raw: str) -> dict:
        """Defensively parse JSON, handling markdown fences if the model adds them."""
        text = raw.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            # Try to find the first { ... } block
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    raise RuntimeError(f"Could not parse LLM JSON output: {e}\nRaw: {raw[:500]}")
            else:
                raise RuntimeError(f"No JSON found in LLM output: {raw[:500]}")

        return self._normalize(data)

    def _normalize(self, data: dict) -> dict:
        """Ensure expected keys exist and numbers are actually numbers."""
        defaults = {
            "vendor_name": None,
            "invoice_number": None,
            "invoice_date": None,
            "due_date": None,
            "currency": None,
            "subtotal": None,
            "tax_amount": None,
            "total_amount": None,
            "buyer_name": None,
            "line_items": [],
        }
        for key, default in defaults.items():
            data.setdefault(key, default)

        # Coerce numeric fields
        for key in ["subtotal", "tax_amount", "total_amount"]:
            data[key] = self._to_number(data.get(key))

        # Clean line items
        clean_items = []
        for item in data.get("line_items") or []:
            if not isinstance(item, dict):
                continue
            clean_items.append({
                "description": item.get("description"),
                "quantity": self._to_number(item.get("quantity")),
                "unit_price": self._to_number(item.get("unit_price")),
                "amount": self._to_number(item.get("amount")),
            })
        data["line_items"] = clean_items

        return data

    @staticmethod
    def _to_number(val) -> Optional[float]:
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            cleaned = re.sub(r"[^\d.\-]", "", val)
            try:
                return float(cleaned) if cleaned else None
            except ValueError:
                return None
        return None


# ── Combined pipeline ─────────────────────────────────────────────────────────

class InvoiceExtractionPipeline:
    """Full pipeline: file bytes -> OCR text -> LLM extraction -> structured dict."""

    def __init__(self, model: str = OLLAMA_MODEL):
        self.text_extractor = TextExtractor()
        self.llm_extractor = LLMExtractor(model=model)

    def health_check(self) -> dict:
        ok, msg = self.llm_extractor.check_connection()
        return {"ollama_connected": ok, "message": msg, "model": self.llm_extractor.model}

    def process(self, file_bytes: bytes, filename: str) -> dict:
        """
        Returns:
            {
              "extracted": {...invoice fields...},
              "ocr_text": "raw text",
              "ocr_method": "pdf_text" | "ocr",
              "llm_raw": "raw llm response",
              "pages": int
            }
        """
        ocr_result = self.text_extractor.extract(file_bytes, filename)

        if not ocr_result["text"].strip():
            raise ValueError("No text could be extracted from this file. The image may be too low quality.")

        extracted, llm_raw = self.llm_extractor.extract(ocr_result["text"])

        return {
            "extracted": extracted,
            "ocr_text": ocr_result["text"],
            "ocr_method": ocr_result["method"],
            "llm_raw": llm_raw,
            "pages": ocr_result["pages"],
        }


# ── CLI test ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python extraction_engine.py <path_to_invoice.pdf|.png|.jpg>")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    pipeline = InvoiceExtractionPipeline()

    health = pipeline.health_check()
    print(f"Ollama health: {health}\n")
    if not health["ollama_connected"]:
        print("Fix Ollama connection before continuing.")
        sys.exit(1)

    file_bytes = file_path.read_bytes()
    result = pipeline.process(file_bytes, file_path.name)

    print("=" * 60)
    print("OCR METHOD:", result["ocr_method"])
    print("PAGES:", result["pages"])
    print("=" * 60)
    print("\nEXTRACTED JSON:")
    print(json.dumps(result["extracted"], indent=2))