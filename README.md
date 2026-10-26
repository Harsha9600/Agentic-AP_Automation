# Agentic AP Invoice Automation

This project extracts AP invoice details from PDF or image uploads using an agentic workflow and vision models. Grok (xAI) is the default provider; OCI Generative AI remains available. It stores one final local JSON dump per invoice.

For the complete architecture, file map, configuration reference, output schema, troubleshooting, and maintenance notes, read the [Project Guide](PROJECT_GUIDE.md).

## Workflow

1. `intake_agent` accepts the uploaded PDF/image.
2. `prompt_agent` loads the extraction prompt from `prompts/invoice_extraction_prompt.md`.
3. `pdf_to_image_agent` renders every PDF page to a PNG image.
4. `page_extraction_agent` processes every page one after another:
   - Runs the copied MTI barcode detector from `barcode_pipeline/local_barcode.py`.
   - Sends the page image and raw barcode JSON to the selected model to identify Code 39 document separators.
   - Sends the page image, extraction prompt, page number, and separator result to the selected model for invoice extraction.
5. `finalizer_agent` merges all page-level JSON into one invoice JSON.
6. `local_dump_agent` writes `outputs/<filename>_ap_extraction.json`.

Barcode detection is implemented in `barcode_pipeline/local_barcode.py`.

## Setup

```powershell
cd C:\Project\Agentic-AP_Automation
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# First-time setup only; keep an existing .env to preserve its OCI settings.
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

### Grok (default)

Create an xAI API key at [console.x.ai](https://console.x.ai/) and set `XAI_API_KEY` in the existing `.env` file:

```dotenv
LLM_PROVIDER=grok
XAI_API_KEY=
GROK_BASE_URL=https://api.x.ai/v1
GROK_MODEL_ID=grok-4.7
GROK_MAX_TOKENS=8000
GROK_TEMPERATURE=0
GROK_TIMEOUT_SECONDS=180
GROK_REASONING_EFFORT=low
```

Set `XAI_API_KEY` to your actual secret locally; never commit it. No extra Python SDK is required.

The default model supports image input and structured output. You can change `GROK_MODEL_ID` to another image-capable model available to your xAI account; leave `GROK_REASONING_EFFORT` blank for models without configurable reasoning. See [xAI model documentation](https://docs.x.ai/developers/models/grok-4.7).

Grok is xAI's paid API, separate from Groq's free developer API. Free web chat access does not provide a recurring free API quota. Any promotional credits depend on your account. This workflow makes two API calls per page. Missing credentials, authentication, billing, or rate-limit errors stop the run with a clear message; there is no automatic provider fallback. See [xAI API pricing](https://x.ai/api).

### OCI (optional)

Keep the existing `OCI_*` settings in `.env`. To use OCI, select `oci` in the sidebar, pass `--provider oci` on the CLI, or change `LLM_PROVIDER=oci`. Grok does not read or validate your OCI credential file.

Check OCI access before real extraction:

```powershell
python diagnose_oci.py
```

## Run Streamlit UI

```powershell
python -m streamlit run streamlit_app.py
```

Open:

```text
http://localhost:8501
```

The sidebar offers `grok`, `oci`, and `mock`. Grok is selected by default. Use `mock` to exercise the local workflow without calling an external model; it does not extract real invoice fields.

## CLI

```powershell
python main.py "C:\path\invoice.pdf" --provider mock
python main.py "C:\path\invoice.pdf" --provider grok
python main.py "C:\path\invoice.pdf" --provider oci
```

## Final JSON

The final JSON includes:

- `header`: merged invoice header from all pages.
- `lineItems`: concatenated and renumbered line items from all pages.
- `barcodeDetails`: barcode values and page-level barcode records.
- `pageAudit`: one record per rendered page, proving every PDF page was considered.
- `processing`: run metadata including page count and model/provider.
