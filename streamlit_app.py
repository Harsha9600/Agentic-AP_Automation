from __future__ import annotations

import base64
import html
import json
import logging
import mimetypes
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import streamlit as st
import streamlit.components.v1 as components

from agent_workflow import process_invoice
from app_models import FinalInvoiceJson
from config import AppConfig
from oci_llm import FatalLlmConfigurationError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

SUPPORTED_TYPES = ["pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"]


def main() -> None:
    st.set_page_config(
        page_title="Agentic AP Invoice Automation",
        page_icon="AP",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _styles()

    config = AppConfig.from_env()
    config.ensure_dirs()

    with st.sidebar:
        st.markdown("### Runtime")
        provider = st.segmented_control(
            "LLM provider",
            ["grok", "oci", "mock"],
            default=config.llm_provider if config.llm_provider in {"grok", "oci", "mock"} else "grok",
        ) or "grok"
        if provider == "grok":
            st.caption(f"Model: {config.grok_model_id}")
            if not config.xai_api_key:
                st.info("Add XAI_API_KEY to .env to enable Grok extraction.")
        dpi = st.slider("PDF render DPI", 120, 360, config.pdf_render_dpi, 20)
        max_side = st.slider("Image max side", 800, 2600, config.image_max_side, 100)
        st.divider()
        st.caption(f"Prompt: `{config.prompt_file}`")
        st.caption(f"Output: `{config.output_dir.resolve()}`")

    _html(
        '<section class="topbar">'
        '<div><p class="eyebrow">Accounts Payable</p><h1>Invoice Review Workspace</h1></div>'
        f'<div class="flow">Agentic OCR + {_escape(provider.upper())} Vision Extraction</div>'
        "</section>"
    )

    uploaded_file = st.file_uploader(
        "Upload invoice PDF or image",
        type=SUPPORTED_TYPES,
        accept_multiple_files=False,
    )

    if uploaded_file is None:
        _empty_state()
        return

    source_path = _save_upload(uploaded_file, config.input_dir)
    run_config = AppConfig(**{**config.__dict__, "llm_provider": provider, "pdf_render_dpi": dpi, "image_max_side": max_side})

    cols = st.columns(4)
    cols[0].metric("File", uploaded_file.name)
    cols[1].metric("Provider", provider.upper())
    cols[2].metric("Size", _format_bytes(uploaded_file.size or source_path.stat().st_size))
    cols[3].metric("Local dump", "Enabled")

    if st.button("Extract Invoice JSON", type="primary", use_container_width=True):
        with st.spinner("Extracting invoice and building the review JSON..."):
            try:
                result = process_invoice(source_path, run_config.output_dir, run_config)
            except FatalLlmConfigurationError as exc:
                st.error(str(exc))
                return
            except Exception as exc:
                st.exception(exc)
                return
        st.session_state["latest_result"] = result
        st.session_state["latest_output_path"] = str(run_config.output_dir / f"{source_path.stem}_ap_extraction.json")

    if "latest_result" in st.session_state:
        _render_result(st.session_state["latest_result"], Path(st.session_state["latest_output_path"]))


def _render_result(result: FinalInvoiceJson, output_path: Path) -> None:
    data = result.model_dump(mode="json")
    page_count = result.processing.get("page_count", len(result.pageAudit))

    if page_count != len(result.pageAudit):
        st.warning("Page count does not match page audit count.")

    _review_summary(result, page_count)

    document_col, details_col = st.columns([1.18, 0.82], gap="large")
    with document_col:
        _render_document_view(result)
    with details_col:
        _render_invoice_details(result)

    _render_line_items(result)

    with st.expander("Page audit and final JSON", expanded=False):
        st.download_button(
            "Download final JSON",
            data=json.dumps(data, indent=2),
            file_name=output_path.name,
            mime="application/json",
            use_container_width=True,
        )
        st.caption(str(output_path.resolve()))
        audit_tab, json_tab = st.tabs(["Page Audit", "JSON"])
        with audit_tab:
            _render_page_audit(result)
        with json_tab:
            st.json(data)


def _review_summary(result: FinalInvoiceJson, page_count: int) -> None:
    header = result.header
    rows = result.lineItems.get("lineItem", [])
    barcode_values = result.barcodeDetails.get("barcode_values", [])
    summary_items = [
        ("Invoice", header.get("INVOICE_NUMBER")),
        ("Vendor", header.get("VENDOR_NAME")),
        ("Amount", _money_value(header.get("INVOICE_AMOUNT"), header.get("CURRENCY_CODE"))),
        ("Pages", page_count),
        ("Lines", len(rows)),
        ("Separators", len(barcode_values)),
    ]
    cards = []
    for label, value in summary_items:
        cards.append(
            '<div class="summary-item">'
            f"<span>{_escape(label)}</span>"
            f"<strong>{_escape(_display_value(value))}</strong>"
            "</div>"
        )
    _html(f'<section class="review-summary">{"".join(cards)}</section>')


def _render_document_view(result: FinalInvoiceJson) -> None:
    pages = result.pageAudit
    _html('<div class="section-label">Document</div>')
    if not pages:
        st.info("No rendered pages were returned.")
        return

    st.session_state.setdefault("document_zoom", 100)
    page_options = ["All pages"] + [f"Page {page.page_number}" for page in pages]
    control_cols = st.columns([2.5, 0.34, 0.34, 0.55, 2.2], gap="small")
    with control_cols[0]:
        selected_page = st.selectbox("Document page", page_options, label_visibility="collapsed")
    with control_cols[1]:
        if st.button("-", key="zoom_out", use_container_width=True):
            st.session_state["document_zoom"] = max(50, st.session_state["document_zoom"] - 10)
    with control_cols[2]:
        if st.button("+", key="zoom_in", use_container_width=True):
            st.session_state["document_zoom"] = min(220, st.session_state["document_zoom"] + 10)
    with control_cols[3]:
        if st.button("Fit", key="zoom_fit", use_container_width=True):
            st.session_state["document_zoom"] = 100
    with control_cols[4]:
        zoom = st.slider(
            "Zoom",
            min_value=50,
            max_value=220,
            value=st.session_state["document_zoom"],
            step=10,
            label_visibility="collapsed",
        )
        st.session_state["document_zoom"] = zoom

    visible_pages = pages
    if selected_page != "All pages":
        selected_number = int(selected_page.replace("Page ", ""))
        visible_pages = [page for page in pages if page.page_number == selected_number]

    _render_zoomable_pages(visible_pages, st.session_state["document_zoom"])


def _render_invoice_details(result: FinalInvoiceJson) -> None:
    _html('<div class="section-label">Extracted Details</div>')
    header = result.header
    fields = [
        ("Vendor", "VENDOR_NAME"),
        ("Invoice Number", "INVOICE_NUMBER"),
        ("Invoice Date", "INVOICE_DATE"),
        ("PO Number", "PO_NUMBER"),
        ("Document Type", "DOCUMENT_TYPE"),
        ("Invoice Type", "INVOICE_TYPE"),
        ("Subtotal", "SUBTOTAL"),
        ("Tax Amount", "TAX_AMOUNT"),
        ("Invoice Amount", "INVOICE_AMOUNT"),
        ("Currency", "CURRENCY_CODE"),
        ("Payment Terms", "PAYMENT_TERMS"),
        ("Customer", "CUSTOMER_NAME"),
        ("Bill To", "BILL_TO_COMPANY_NAME"),
        ("Bill To Country", "BILL_TO_COUNTRY_CODE"),
        ("Vendor City", "VENDOR_CITY"),
        ("Vendor Country", "VENDOR_COUNTRY_CODE"),
        ("Identification", "IDENTIFICATION_NUMBER"),
        ("Language", "LANGUAGE_CODE"),
    ]

    detail_fields = []
    for label, key in fields:
        value = header.get(key)
        detail_fields.append(
            '<div class="detail-field">'
            f"<span>{_escape(label)}</span>"
            f"<strong>{_escape(_display_value(value))}</strong>"
            "</div>"
        )
    _html(f'<section class="details-grid">{"".join(detail_fields)}</section>')
    _html(_barcode_summary_html(result.barcodeDetails))


def _render_line_items(result: FinalInvoiceJson) -> None:
    rows = result.lineItems.get("lineItem", [])
    _html('<div class="line-title">Line Items</div>')
    if not rows:
        st.info("No line items extracted.")
        return

    ordered_columns = [
        "LINE_NUMBER",
        "ITEM_CODE",
        "LINE_DESCRIPTION",
        "QUANTITY",
        "UNIT_OF_MEASURE",
        "UNIT_PRICE",
        "LINE_AMOUNT",
        "LINE_TAX_AMOUNT",
        "TAX_RATE",
        "LINE_TYPE",
    ]
    normalized_rows = [_normalize_line_row(row, ordered_columns) for row in rows]
    table_height = min(620, max(180, 38 * (len(normalized_rows) + 1)))
    st.dataframe(normalized_rows, hide_index=True, use_container_width=True, height=table_height)


def _render_page_audit(result: FinalInvoiceJson) -> None:
    for page in result.pageAudit:
        title = _page_title(page)
        if page.extraction_error:
            title += " - extraction warning"
        with st.expander(title, expanded=False):
            st.markdown("##### LLM Document Separator Barcode")
            st.json(_separator_barcode_view(page.document_separator_detection))
            with st.expander("Raw MTI barcode detection", expanded=False):
                st.json(page.barcode_detection)
            st.markdown("##### Page LLM JSON")
            st.json(page.llm_json)
            if page.extraction_error:
                st.error(page.extraction_error)


def _render_zoomable_pages(pages: list[Any], zoom: int) -> None:
    page_blocks = []
    for page in pages:
        image_path = Path(page.image_path)
        if image_path.exists():
            image_markup = f'<img class="invoice-page-image" src="{_image_data_uri(image_path)}" alt="Page {_escape(page.page_number)}" />'
        else:
            image_markup = f'<div class="viewer-missing">{_escape(page.image_path)}</div>'
        error_markup = f'<div class="viewer-error">{_escape(page.extraction_error)}</div>' if page.extraction_error else ""
        page_blocks.append(
            '<article class="invoice-page-shell">'
            f"{_page_header_html(page)}"
            f"{image_markup}"
            f"{error_markup}"
            "</article>"
        )

    viewer_html = (
        "<!doctype html><html><head><style>"
        "html,body{margin:0;background:#fbfcfe;color:#182235;font-family:Arial,sans-serif;}"
        ".invoice-viewer{box-sizing:border-box;height:760px;overflow:auto;background:#fbfcfe;border:1px solid #d8dee8;border-radius:8px;padding:14px;}"
        ".invoice-page-shell{margin:0 auto 18px;width:100%;}"
        ".invoice-page-image{display:block;width:"
        f"{zoom}%"
        ";max-width:none;height:auto;border:1px solid #d8dee8;border-radius:4px;background:#fff;box-shadow:0 1px 2px rgba(15,23,42,.08);}"
        ".page-header{align-items:center;border-bottom:1px solid #e7ebf1;display:flex;justify-content:space-between;margin:0 0 10px;padding:4px 0 8px;}"
        ".page-header span:first-child{color:#182235;font-size:14px;font-weight:700;}"
        ".page-badge{background:#eef2f6;border:1px solid #d8dee8;border-radius:999px;color:#4d5a6c;font-size:11px;font-weight:700;line-height:1;padding:5px 8px;white-space:nowrap;}"
        ".page-badge.separator{background:#fff7ed;border-color:#fed7aa;color:#8a3f12;}"
        ".viewer-missing,.viewer-error{border:1px solid #d8dee8;border-radius:8px;padding:12px;background:#fff;}"
        ".viewer-error{border-color:#fecaca;background:#fff1f2;color:#991b1b;margin-top:8px;}"
        "</style></head><body>"
        f'<div class="invoice-viewer">{"".join(page_blocks)}</div>'
        "</body></html>"
    )
    components.html(viewer_html, height=780, scrolling=False)


def _page_header_html(page) -> str:
    detection = page.document_separator_detection or {}
    if detection.get("is_document_separator"):
        value = detection.get("barcode_value") or "Code 39"
        badge = f'<span class="page-badge separator">Separator: {_escape(value)}</span>'
    else:
        badge = '<span class="page-badge">Invoice page</span>'
    return f'<div class="page-header"><span>Page {_escape(page.page_number)}</span>{badge}</div>'


def _barcode_summary_html(barcode_details: dict[str, Any]) -> str:
    has_barcode = barcode_details.get("has_barcode")
    pages = barcode_details.get("barcode_pages") or []
    values = barcode_details.get("barcode_values") or []
    status = "Found" if has_barcode else "Not found"
    status_class = "found" if has_barcode else "missing"
    page_value = ", ".join(str(page) for page in pages) or "-"
    barcode_value = ", ".join(str(value) for value in values) or "-"
    source = barcode_details.get("decision_source") or "llm_vision"
    return (
        '<section class="barcode-panel">'
        f'<div><span>Document Separator Barcode</span><strong class="{status_class}">{_escape(status)}</strong></div>'
        f"<div><span>Pages</span><strong>{_escape(page_value)}</strong></div>"
        f"<div><span>Value</span><strong>{_escape(barcode_value)}</strong></div>"
        f"<div><span>Source</span><strong>{_escape(source)}</strong></div>"
        "</section>"
    )


def _normalize_line_row(row: dict[str, Any], ordered_columns: list[str]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for column in ordered_columns:
        normalized[column] = _display_value(row.get(column), blank="")
    for key, value in row.items():
        if key not in normalized:
            normalized[key] = _display_value(value, blank="")
    return normalized


def _save_upload(uploaded_file, input_dir: Path) -> Path:
    input_dir.mkdir(parents=True, exist_ok=True)
    upload_key = f"{uploaded_file.name}:{uploaded_file.size}"
    if st.session_state.get("upload_key") == upload_key:
        existing = st.session_state.get("source_path")
        if existing and Path(existing).exists():
            return Path(existing)

    target = input_dir / f"{uuid4().hex}_{Path(uploaded_file.name).name}"
    uploaded_file.seek(0)
    with target.open("wb") as handle:
        shutil.copyfileobj(uploaded_file, handle)

    st.session_state["upload_key"] = upload_key
    st.session_state["source_path"] = str(target)
    st.session_state.pop("latest_result", None)
    st.session_state.pop("latest_output_path", None)
    return target


def _separator_barcode_view(detection: dict) -> dict:
    return {
        "decision_source": detection.get("source", "llm_vision"),
        "is_document_separator": bool(detection.get("is_document_separator")),
        "barcode_type": detection.get("barcode_type") if detection.get("is_document_separator") else None,
        "barcode_value": detection.get("barcode_value") if detection.get("is_document_separator") else None,
        "confidence": detection.get("confidence"),
        "evidence": detection.get("evidence"),
    }


def _page_title(page) -> str:
    detection = page.document_separator_detection or {}
    if detection.get("is_document_separator"):
        value = detection.get("barcode_value")
        suffix = f" ({value})" if value else ""
        return f"Page {page.page_number} - LLM Code 39 separator{suffix}"
    return f"Page {page.page_number}"


def _empty_state() -> None:
    _html(
        '<div class="empty">'
        "<h2>Upload an invoice to begin</h2>"
        "<p>Every page is rendered, extracted, reviewed, and written to one local JSON dump.</p>"
        "</div>"
    )


def _html(content: str) -> None:
    if hasattr(st, "html"):
        st.html(content)
    else:
        st.markdown(content, unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def _image_data_uri(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _display_value(value: Any, blank: str = "-") -> str:
    if value is None:
        return blank
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else blank
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _money_value(amount: Any, currency: Any) -> str:
    amount_value = _display_value(amount, blank="")
    currency_value = _display_value(currency, blank="")
    if amount_value and currency_value:
        return f"{amount_value} {currency_value}"
    return amount_value or currency_value or "-"


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _styles() -> None:
    st.markdown(
        """
        <style>
        .block-container { max-width: 1540px; padding-top: 1.4rem; }
        .topbar {
          align-items: center;
          border-bottom: 1px solid #d8dee8;
          display: flex;
          gap: 1rem;
          justify-content: space-between;
          margin-bottom: 1rem;
          padding-bottom: 0.85rem;
        }
        .topbar h1 {
          color: #182235;
          font-size: 1.85rem;
          line-height: 1.1;
          margin: 0;
        }
        .eyebrow {
          color: #1b7f72;
          font-size: 0.78rem;
          font-weight: 700;
          letter-spacing: 0;
          margin: 0 0 0.35rem;
          text-transform: uppercase;
        }
        .flow {
          background: #f7f9fc;
          border: 1px solid #d8dee8;
          border-radius: 8px;
          color: #4d5a6c;
          font-size: 0.86rem;
          padding: 0.55rem 0.75rem;
          white-space: nowrap;
        }
        .review-summary {
          display: grid;
          gap: 0.7rem;
          grid-template-columns: repeat(6, minmax(0, 1fr));
          margin: 1rem 0 1.1rem;
        }
        .summary-item {
          background: #ffffff;
          border: 1px solid #d8dee8;
          border-radius: 8px;
          min-width: 0;
          padding: 0.72rem 0.8rem;
        }
        .summary-item span,
        .detail-field span,
        .barcode-panel span {
          color: #627084;
          display: block;
          font-size: 0.73rem;
          font-weight: 700;
          line-height: 1.2;
          margin-bottom: 0.28rem;
          text-transform: uppercase;
        }
        .summary-item strong {
          color: #182235;
          display: block;
          font-size: 0.94rem;
          line-height: 1.25;
          overflow-wrap: anywhere;
        }
        .section-label,
        .line-title {
          color: #182235;
          font-size: 1.02rem;
          font-weight: 750;
          line-height: 1.2;
          margin: 0.35rem 0 0.55rem;
        }
        .details-grid {
          display: grid;
          gap: 0.62rem;
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        .detail-field {
          background: #ffffff;
          border: 1px solid #d8dee8;
          border-radius: 8px;
          min-height: 76px;
          min-width: 0;
          padding: 0.7rem 0.78rem;
        }
        .detail-field strong {
          color: #182235;
          display: block;
          font-size: 0.92rem;
          font-weight: 650;
          line-height: 1.25;
          overflow-wrap: anywhere;
        }
        .barcode-panel {
          background: #f7f9fc;
          border: 1px solid #cdd6e3;
          border-radius: 8px;
          display: grid;
          gap: 0.75rem;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          margin-top: 0.9rem;
          padding: 0.78rem;
        }
        .barcode-panel strong {
          color: #182235;
          display: block;
          font-size: 0.92rem;
          line-height: 1.25;
          overflow-wrap: anywhere;
        }
        .barcode-panel .found { color: #0f766e; }
        .barcode-panel .missing { color: #8a3f12; }
        .page-header {
          align-items: center;
          border-bottom: 1px solid #e7ebf1;
          display: flex;
          justify-content: space-between;
          margin: 0 0 0.75rem;
          padding: 0.35rem 0 0.55rem;
        }
        .page-header span:first-child {
          color: #182235;
          font-size: 0.92rem;
          font-weight: 750;
        }
        .page-badge {
          background: #eef2f6;
          border: 1px solid #d8dee8;
          border-radius: 999px;
          color: #4d5a6c;
          font-size: 0.72rem;
          font-weight: 700;
          line-height: 1;
          padding: 0.32rem 0.52rem;
          white-space: nowrap;
        }
        .page-badge.separator {
          background: #fff7ed;
          border-color: #fed7aa;
          color: #8a3f12;
        }
        .line-title {
          border-top: 1px solid #d8dee8;
          margin-top: 1.15rem;
          padding-top: 1rem;
        }
        .empty {
          align-items: center;
          background: #f7f9fc;
          border: 1px dashed #b9c3d2;
          border-radius: 8px;
          display: flex;
          flex-direction: column;
          justify-content: center;
          min-height: 280px;
          padding: 2rem;
          text-align: center;
        }
        .empty h2 { color: #1f2a44; margin-bottom: 0.5rem; }
        .empty p { color: #596579; margin: 0; max-width: 760px; }
        div[data-testid="stMetric"] {
          background: #f7f9fc;
          border: 1px solid #d8dee8;
          border-radius: 8px;
          padding: 0.8rem;
        }
        .stButton > button, .stDownloadButton > button { border-radius: 8px; }
        div[data-testid="stDataFrameResizable"] { border-radius: 8px; }
        div[data-testid="stVerticalBlockBorderWrapper"] {
          background: #fbfcfe;
        }
        img {
          border: 1px solid #e1e7ef;
          border-radius: 4px;
        }
        @media (max-width: 1180px) {
          .review-summary { grid-template-columns: repeat(3, minmax(0, 1fr)); }
          .details-grid { grid-template-columns: 1fr; }
        }
        @media (max-width: 760px) {
          .topbar { align-items: flex-start; flex-direction: column; }
          .flow { white-space: normal; }
          .review-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
          .barcode-panel { grid-template-columns: 1fr; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
