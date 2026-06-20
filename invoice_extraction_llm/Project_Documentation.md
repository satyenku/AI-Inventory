# Gemini Invoice Processor - Clean Deployment

The `gemini-invoice-processor` is now a completely independent, production-ready system. It has been detached from all external folders (including `AI-Inventory`) and contains its own integrated master database.

## System status
*   **Database:** `invoices.db` (Located in the root folder).
*   **Entry Point:** `app.py`
*   **Primary UI:** `item_entry.html` (Accessible at the root `/` URL).
*   **AI Engine:** Google Gemini (Multi-model fallback chain).

## How to Run
1.  Ensure your `.env` file contains your `GEMINI_API_KEY`.
2.  Run the application:
    ```bash
    python app.py
    ```
3.  Open your browser and naturally navigate to:
    **[http://127.0.0.1:5000/](http://127.0.0.1:5000/)**

## New Relational Features
*   **Selective Saving:** You can now upload an invoice, review the lines, and click **"Add Item"** on only the specific products you want to keep.
*   **Relational Storage:** When you click **"Save Invoice"**, the system correctly splits the data:
    *   **Header Info:** Saved to the master `invoices` table.
    *   **Items:** Saved to the `invoice_items` table, linked automatically via `invoice_id`.

## Security & Privacy
*   **Automatic Cleanup:** All uploaded PDF/Image files are deleted from the `uploads/` folder immediately after AI extraction to protect privacy.
*   **Safe Execution:** No external folders are required anymore. The `AI-Inventory` folder has been safely removed.
