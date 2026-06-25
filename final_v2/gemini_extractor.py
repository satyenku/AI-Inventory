# gemini_extractor.py
import os
import time
from dotenv import load_dotenv
load_dotenv()

from pydantic import BaseModel, Field
from typing import List
from google import genai
from google.genai import types

class InvoiceItem(BaseModel):
    description: str = Field(description="The clear corporate product or service item description.")
    qty: str = Field(description="Quantity ordered.")
    unit_price: str = Field(description="Per-unit item price rate.")
    amount: str = Field(description="Total aggregate line rate price.")

class InvoiceExtractionSchema(BaseModel):
    invoice_number: str = Field(description="Unique string identifiers denoting billing sequence numbers.")
    invoice_date: str = Field(description="Standardized date of billing as YYYY-MM-DD.")
    vendor_name: str = Field(description="The corporate legal entity name representing supplier.")
    tax_amount_clean: str = Field(description="The clean isolated numeric tax/VAT details (e.g. 150.00).")
    line_items: List[InvoiceItem] = Field(description="Line items.")
    total_amount: float = Field(description="Aggregate check total including taxes.")

def extract_invoice_data(file_path: str) -> InvoiceExtractionSchema:
    prompt = (
        "Process this transaction invoice or receipt and isolate all values matching schema formats. "
        "Return only plain, machine-readable values. "
        "Dates must be ISO format YYYY-MM-DD. "
        "Quantities, unit prices, amounts, and totals must be numeric strings without comma grouping separators or currency symbols. "
        "Line item descriptions should be clear and concise. "
        "Do not include extra text outside the JSON schema output."
    )

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY value is missing.")

    MODELS = [
        'gemini-2.5-flash-lite',
        'gemini-3.5-flash-lite',
        'gemini-3.5-flash',
        'gemini-2.5-flash',
    ]

    client = genai.Client(api_key=api_key)
    last_error = None

    for model in MODELS:
        uploaded_file = None
        try:
            uploaded_file = client.files.upload(file=file_path)

            response = client.models.generate_content(
                model=model,
                contents=[uploaded_file, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=InvoiceExtractionSchema,
                    temperature=0.1
                ),
            )
            return response.parsed
        except Exception as e:
            last_error = e
            time.sleep(1)
            continue
        finally:
            if uploaded_file:
                try:
                    client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass

    raise RuntimeError(f"All processing attempts failed. Last exception details: {last_error}")