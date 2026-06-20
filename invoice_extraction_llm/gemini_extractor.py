import os
import time
from dotenv import load_dotenv
load_dotenv()

from pydantic import BaseModel, Field
from typing import List
from google import genai
from google.genai import types

class InvoiceItem(BaseModel):
    description: str = Field(description="The exact product or service name as written on the invoice (e.g., 'Chair', 'Sofa', 'Web Development Service'). Never use generic placeholders like 'Item' or 'Item Item'.")
    qty: str = Field(description="Quantity ordered or processed.")
    unit_price: str = Field(description="Unit price or rate of the line item.")
    amount: str = Field(description="Total calculated amount for this specific item row.")

class InvoiceExtractionSchema(BaseModel):
    invoice_number: str = Field(description="The unique invoice number string.")
    invoice_date: str = Field(description="The billing date standardizing to YYYY-MM-DD.")
    vendor_name: str = Field(description="The legal corporate name of the vendor selling.")
    tax_amount_clean: str = Field(description="Extract ONLY the numeric tax/VAT label and amount (e.g., 'TAX $139.15' or 'VAT (7%) $126.00'). If no explicit tax is on the invoice, return 'No explicit tax listed.'.")
    line_items: List[InvoiceItem] = Field(description="List of all individual line items found in the main table layout.")
    total_amount: float = Field(description="The absolute total monetary amount due.")

def extract_invoice_data(file_path: str) -> InvoiceExtractionSchema:
    """Uploads the file to Gemini and extracts structured invoice data with retry logic."""
    
    prompt = (
        "Analyze this invoice document and extract all fields requested in the response schema. "
        "CRITICAL RULES FOR LINE ITEMS: "
        "1. Extract EVERY row from the invoice's item/product table as a separate line_item. "
        "2. For each item, 'description' must be the ACTUAL product/service name exactly as printed (e.g., 'Chair', 'Sofa', 'Laptop'), NOT generic words like 'Item'. "
        "3. 'qty' must be the quantity number, 'unit_price' must be the per-unit rate/price, 'amount' must be the row total. "
        "4. Do NOT merge multiple rows into one. Do NOT skip any rows. "
        "5. Do not bundle structural table headers or column names into the tax field."
    )

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not found. Make sure it is set in your .env file.")

    # Fallback chain: tries each model in order until one succeeds
    MODELS = [
        'gemini-2.0-flash-lite',
        'gemini-2.5-flash-lite',
        'gemini-2.0-flash',
        'gemini-3.5-flash',
        'gemini-2.5-flash',
    ]

    client = genai.Client(api_key=api_key)
    last_error = None

    for model in MODELS:
        uploaded_file = None
        try:
            uploaded_file = client.files.upload(file=file_path)
            print(f"[Gemini] Trying model: {model}")

            response = client.models.generate_content(
                model=model,
                contents=[uploaded_file, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=InvoiceExtractionSchema,
                    temperature=0.1
                ),
            )

            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass

            print(f"[Gemini] Success with model: {model}")
            return response.parsed

        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            print(f"[Gemini] Model {model} failed: {e}")

            # Clean up uploaded file before trying next model
            if uploaded_file is not None:
                try:
                    client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass

            # Brief pause before trying next model
            is_retryable = any(k in error_str for k in [
                '503', '429', '500', 'overloaded', 'rate', 'quota',
                'unavailable', 'timeout', 'resource_exhausted', 'internal', 'demand'
            ])
            if is_retryable:
                time.sleep(3)
            else:
                # Non-transient error (e.g. 404 not found) — skip immediately
                pass
            continue

    raise RuntimeError(f"All models failed. Last error: {last_error}")