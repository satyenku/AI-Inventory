# Invoice OCR + Local LLM — Invoice → JSON Pipeline

A fully open source, fully local pipeline that extracts structured JSON from invoice images and PDFs.

```
Invoice (PDF/Image) → OCR / direct text extraction → Local LLM (Ollama) → Structured JSON → SQLite
```

**No training. No API keys. No cloud. No cost.** Everything runs on your machine.

---

## How it works

1. **Digital PDFs** (created by software like Tally, Zoho, Word) — text is extracted directly via `pdfplumber`. Perfectly accurate, instant.
2. **Scanned PDFs / images** — converted to images and read via `PaddleOCR`, which returns text in reading order.
3. **The extracted text** (however messy) is sent to a local LLM running via **Ollama**, with a prompt asking it to return structured JSON.
4. **The JSON** is validated, normalized, and saved to SQLite via FastAPI.

Because the LLM works on _text_, not pixels, it generalizes across wildly different invoice layouts without any training — it's just reading.

---

## Project Structure

```
invoice_ocr_llm/
├── backend/
│   ├── extraction_engine.py   ← OCR + LLM pipeline (can run standalone for testing)
│   ├── main.py                ← FastAPI server
│   └── requirements.txt
└── frontend/
    └── index.html             ← Dashboard UI
```

---

## Step 1 — Install Ollama

Ollama runs open source, locally. It's free, open source (MIT), and works on macOS, Linux, and Windows.

**macOS:**

```bash
brew install ollama
```

**Linux:**

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

**Windows:**

```powershell
winget install Ollama.Ollama
```

If `winget` is not available, download the Windows installer from [ollama.com/download](https://ollama.com/download).

**Start the Ollama server** (keep this running in a terminal):

```bash
ollama serve
```

**Pull a model** (one-time download, ~4.5GB):

```bash
ollama pull qwen2.5:7b
```

### Choosing a model

| Model         | Size   | Speed on CPU (MacBook Air) | Quality                                 |
| ------------- | ------ | -------------------------- | --------------------------------------- |
| `qwen2.5:3b`  | ~2GB   | Faster (~10-20s/invoice)   | Good — try this first on older hardware |
| `qwen2.5:7b`  | ~4.5GB | Slower (~30-60s/invoice)   | Better accuracy                         |
| `phi3:mini`   | ~2.3GB | Fast                       | Good alternative                        |
| `llama3.2:3b` | ~2GB   | Fast                       | Good alternative                        |

**For your 2015 MacBook Air (8GB RAM, no dedicated GPU), start with `qwen2.5:3b` or `phi3:mini`.** The 7B model will work but each invoice may take 30-60+ seconds.

```bash
ollama pull qwen2.5:3b
```

Then set this in the backend (see Step 3):

```bash
export OLLAMA_MODEL=qwen2.5:3b
```

---

## Step 2 — Install system dependencies

**Poppler** (for PDF-to-image conversion):

```bash
# macOS
brew install poppler

# Linux
sudo apt install poppler-utils
```

**Windows**
Download a Windows Poppler build from the official releases page:
https://github.com/oschwartz10612/poppler-windows/releases

Extract the archive and add the `poppler/bin` folder to your PATH so `pdftoppm` is available.

---

## Step 3 — Run the Backend

Create and activate a Python virtual environment first, then install dependencies.

**macOS / Linux:**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt --break-system-packages
```

**Windows PowerShell:**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Windows cmd.exe:**

```cmd
cd backend
python -m venv .venv
.\.venv\Scripts\activate.bat
pip install -r requirements.txt
```

Set the Ollama model name to match the model you pulled:

**macOS / Linux:**

```bash
export OLLAMA_MODEL=qwen2.5:3b
```

**Windows PowerShell:**

```powershell
$env:OLLAMA_MODEL = "qwen2.5:3b"
```

**Windows cmd.exe:**

```cmd
set OLLAMA_MODEL=qwen2.5:3b
```

Then run:

```bash
uvicorn main:app --reload --port 8000
```

First startup will take a moment as PaddleOCR downloads its detection models (~10MB, one-time, cached afterward).

API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## Step 4 — Open the Frontend

Open `frontend/index.html` in your browser.

- Top-right status pill shows if Ollama is connected and which model is active
- Drag and drop invoices (PDF/JPG/PNG)
- Each invoice shows an `ocr_method` badge: `pdf_text` (digital PDF, instant + accurate) or `ocr` (scanned, via PaddleOCR)
- **"OCR / LLM Debug" tab** — shows you the raw OCR text and raw LLM response for every invoice. Use this to tune your prompt if extraction quality is poor.

---

## Testing the pipeline standalone (without the web UI)

```bash
cd backend
python extraction_engine.py /path/to/invoice.pdf
```

This prints the OCR method used and the extracted JSON directly — useful for debugging before involving the API/frontend.

---

## API Reference

| Method   | Endpoint               | Description                            |
| -------- | ---------------------- | -------------------------------------- |
| `GET`    | `/`                    | Health check, Ollama connection status |
| `POST`   | `/invoices/upload`     | Upload invoice (JPG/PNG/PDF)           |
| `GET`    | `/invoices`            | List all invoices                      |
| `GET`    | `/invoices/{id}`       | Get single invoice                     |
| `GET`    | `/invoices/{id}/debug` | Raw OCR text + raw LLM response        |
| `DELETE` | `/invoices/{id}`       | Delete invoice                         |

---

## Improving Extraction Accuracy

**1. Tune the prompt** — open `extraction_engine.py`, find `EXTRACTION_PROMPT_TEMPLATE`. This is plain English — you can add rules like:

```
- If multiple "total" values appear, prefer the one labeled "Grand Total" or "Amount Due"
- Dates may appear in DD/MM/YYYY format — preserve as written
```

**2. Use the Debug tab** — for any invoice that extracts incorrectly, check the "OCR / LLM Debug" tab. If the OCR text itself is garbled, the problem is image quality/OCR. If OCR text looks fine but the JSON is wrong, the problem is the prompt — tune it.

**3. Try a larger model** — if `qwen2.5:3b` struggles with complex invoices, `qwen2.5:7b` or `qwen2.5:14b` (if your hardware allows) will be more accurate at the cost of speed.

**4. Improve OCR for scanned documents** — PaddleOCR works best on clean, high-contrast scans. If scans are skewed or low-resolution, consider pre-processing with Pillow (deskew, increase contrast) before OCR.

---

## Open Source Licenses

All components are free and open source — safe for client delivery:

| Component       | License    |
| --------------- | ---------- |
| Ollama          | MIT        |
| Qwen2.5 (model) | Apache 2.0 |
| PaddleOCR       | Apache 2.0 |
| pdfplumber      | MIT        |
| FastAPI         | MIT        |
| SQLAlchemy      | MIT        |

---

## Troubleshooting

**"Ollama not ready" banner:** Run `ollama serve` in a terminal and keep it running. Verify with `ollama list` to confirm your model is pulled.

**Very slow processing (>2 min per invoice):** Switch to a smaller model — `qwen2.5:3b` or `phi3:mini`. CPU inference on older hardware is inherently slow; this is expected.

**Extraction returns mostly nulls:** Check the Debug tab. If `ocr_text` is garbled or empty, the issue is image quality. If `ocr_text` looks readable but JSON is empty, tune the prompt in `extraction_engine.py`.

**PDF support not working:** Confirm poppler is installed — `pdftoppm -v` should print a version number.

**PaddleOCR install issues:** PaddleOCR's pip install can be slow and large (~500MB with dependencies). Ensure you have a stable connection; first run also downloads model weights (~10MB).

**CORS errors in frontend:** Backend has CORS enabled for all origins. If issues persist, serve the frontend via `python -m http.server 3000` instead of opening the file directly.
