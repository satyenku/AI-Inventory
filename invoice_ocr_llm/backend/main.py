"""
FastAPI Backend — Invoice Processing with OCR + Local LLM (Ollama)

Endpoints:
  GET    /                       — Health check (Ollama status)
  POST   /invoices/upload        — Upload invoice image or PDF, extract JSON, save to DB
  GET    /invoices               — List all saved invoices
  GET    /invoices/{id}          — Get a single invoice by ID
  GET    /invoices/{id}/debug    — Get raw OCR text + raw LLM output (for debugging)
  DELETE /invoices/{id}          — Delete an invoice

Run:
  pip install fastapi uvicorn sqlalchemy python-multipart pillow pdf2image \
              pdfplumber paddleocr paddlepaddle requests aiofiles
  uvicorn main:app --reload --port 8000

Requires Ollama running locally:
  ollama serve
  ollama pull qwen2.5:7b
"""

import json
import os
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Float, DateTime, Text, Integer
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from extraction_engine import InvoiceExtractionPipeline

# ── Configuration ──────────────────────────────────────────────────────────────

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./invoices.db")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# ── Database setup ─────────────────────────────────────────────────────────────

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class InvoiceRecord(Base):
    __tablename__ = "invoices"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String, nullable=False)
    vendor_name = Column(String)
    buyer_name = Column(String)
    invoice_number = Column(String)
    invoice_date = Column(String)
    due_date = Column(String)
    currency = Column(String)
    subtotal = Column(Float)
    tax_amount = Column(Float)
    total_amount = Column(Float)
    line_items_json = Column(Text)
    ocr_text = Column(Text)        # raw OCR output, for debugging
    ocr_method = Column(String)    # "pdf_text" | "ocr"
    llm_raw = Column(Text)         # raw LLM response, for debugging
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="success")  # success | failed


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None


class InvoiceOut(BaseModel):
    id: str
    filename: str
    vendor_name: Optional[str]
    buyer_name: Optional[str]
    invoice_number: Optional[str]
    invoice_date: Optional[str]
    due_date: Optional[str]
    currency: Optional[str]
    subtotal: Optional[float]
    tax_amount: Optional[float]
    total_amount: Optional[float]
    line_items: List[LineItem] = []
    ocr_method: Optional[str]
    created_at: datetime
    status: str

    model_config = {"from_attributes": True}


class DebugOut(BaseModel):
    id: str
    ocr_text: str
    ocr_method: str
    llm_raw: str


# ── App setup ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Invoice OCR+LLM API",
    description="Upload invoice images or PDFs, extract text via OCR, "
                 "and structure it into JSON using a local LLM via Ollama.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

pipeline: Optional[InvoiceExtractionPipeline] = None


@app.on_event("startup")
async def init_pipeline():
    global pipeline
    pipeline = InvoiceExtractionPipeline(model=OLLAMA_MODEL)
    health = pipeline.health_check()
    print(f"Ollama health check: {health}")


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    health = pipeline.health_check() if pipeline else {"ollama_connected": False, "message": "Pipeline not initialized"}
    return {
        "service": "Invoice OCR+LLM API",
        "ollama": health,
        "docs": "/docs",
    }


@app.post("/invoices/upload", response_model=InvoiceOut)
async def upload_invoice(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Upload an invoice image (JPG/PNG) or PDF.
    Runs OCR (or direct text extraction for digital PDFs), then a local LLM
    extracts structured fields. Multi-page documents are concatenated before
    extraction since totals/headers are usually on page 1.
    """
    allowed_types = {"image/jpeg", "image/png", "image/jpg", "application/pdf"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Use JPG, PNG, or PDF."
        )

    file_bytes = await file.read()
    filename = file.filename or "upload"

    try:
        result = pipeline.process(file_bytes, filename)
        extracted = result["extracted"]
        status = "success"
        ocr_text = result["ocr_text"]
        ocr_method = result["ocr_method"]
        llm_raw = result["llm_raw"]
    except Exception as e:
        extracted = {
            "vendor_name": None, "buyer_name": None, "invoice_number": None,
            "invoice_date": None, "due_date": None, "currency": None,
            "subtotal": None, "tax_amount": None, "total_amount": None,
            "line_items": [],
        }
        status = "failed"
        ocr_text = ""
        ocr_method = "error"
        llm_raw = str(e)

    record = InvoiceRecord(
        id=str(uuid.uuid4()),
        filename=filename,
        vendor_name=extracted.get("vendor_name"),
        buyer_name=extracted.get("buyer_name"),
        invoice_number=extracted.get("invoice_number"),
        invoice_date=extracted.get("invoice_date"),
        due_date=extracted.get("due_date"),
        currency=extracted.get("currency"),
        subtotal=extracted.get("subtotal"),
        tax_amount=extracted.get("tax_amount"),
        total_amount=extracted.get("total_amount"),
        line_items_json=json.dumps(extracted.get("line_items", [])),
        ocr_text=ocr_text,
        ocr_method=ocr_method,
        llm_raw=llm_raw,
        status=status,
    )

    db.add(record)
    db.commit()
    db.refresh(record)

    return _record_to_schema(record)


@app.get("/invoices", response_model=List[InvoiceOut])
def list_invoices(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """List all invoices, newest first."""
    invoices = (
        db.query(InvoiceRecord)
        .order_by(InvoiceRecord.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_record_to_schema(r) for r in invoices]


@app.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: str, db: Session = Depends(get_db)):
    record = db.query(InvoiceRecord).filter(InvoiceRecord.id == invoice_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return _record_to_schema(record)


@app.get("/invoices/{invoice_id}/debug", response_model=DebugOut)
def get_debug_info(invoice_id: str, db: Session = Depends(get_db)):
    """Get raw OCR text and raw LLM output — useful for tuning prompts."""
    record = db.query(InvoiceRecord).filter(InvoiceRecord.id == invoice_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return DebugOut(
        id=record.id,
        ocr_text=record.ocr_text or "",
        ocr_method=record.ocr_method or "",
        llm_raw=record.llm_raw or "",
    )


@app.delete("/invoices/{invoice_id}")
def delete_invoice(invoice_id: str, db: Session = Depends(get_db)):
    record = db.query(InvoiceRecord).filter(InvoiceRecord.id == invoice_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Invoice not found")
    db.delete(record)
    db.commit()
    return {"deleted": invoice_id}


# ── Helper ─────────────────────────────────────────────────────────────────────

def _record_to_schema(record: InvoiceRecord) -> InvoiceOut:
    try:
        line_items = json.loads(record.line_items_json or "[]")
    except (json.JSONDecodeError, TypeError):
        line_items = []

    return InvoiceOut(
        id=record.id,
        filename=record.filename,
        vendor_name=record.vendor_name,
        buyer_name=record.buyer_name,
        invoice_number=record.invoice_number,
        invoice_date=record.invoice_date,
        due_date=record.due_date,
        currency=record.currency,
        subtotal=record.subtotal,
        tax_amount=record.tax_amount,
        total_amount=record.total_amount,
        line_items=[LineItem(**item) for item in line_items],
        ocr_method=record.ocr_method,
        created_at=record.created_at,
        status=record.status,
    )
