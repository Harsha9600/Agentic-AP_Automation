from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from pydantic import BaseModel, Field


HEADER_TEMPLATE: dict[str, Any] = {
    "VENDOR_NAME": None,
    "CUSTOMER_ADDRESS": None,
    "CUSTOMER_CITY": None,
    "CUSTOMER_NAME": None,
    "BILL_TO_COMPANY_NAME": None,
    "BILL_TO_ADDRESS": None,
    "BILL_TO_COUNTRY_CODE": None,
    "BILL_TO_CITY": None,
    "ADDRESS_LINE1": None,
    "ADDRESS_LINE2": None,
    "VENDOR_CITY": None,
    "VENDOR_STATE": None,
    "VENDOR_COUNTRY_CODE": None,
    "VENDOR_POSTAL_CODE": None,
    "PAYMENT_TERMS": None,
    "PO_NUMBER": None,
    "INVOICE_NUMBER": None,
    "DOCUMENT_TYPE": "NON-PO",
    "INVOICE_TYPE": "STANDARD",
    "INVOICE_DATE": None,
    "INVOICE_AMOUNT": None,
    "CURRENCY_CODE": None,
    "TAX_AMOUNT": None,
    "SUBTOTAL": None,
    "LANGUAGE_CODE": "UNK",
    "IDENTIFICATION_NUMBER": None,
}

LINE_TEMPLATE: dict[str, Any] = {
    "LINE_TYPE": None,
    "LINE_AMOUNT": None,
    "ITEM_CODE": None,
    "LINE_DESCRIPTION": None,
    "QUANTITY": None,
    "UNIT_OF_MEASURE": None,
    "UNIT_PRICE": None,
    "LINE_TAX_AMOUNT": None,
    "TAX_RATE": None,
    "LINE_NUMBER": None,
}


class PageResult(BaseModel):
    page_number: int
    image_path: str
    barcode_detection: dict[str, Any] = Field(default_factory=dict)
    document_separator_detection: dict[str, Any] = Field(default_factory=dict)
    llm_json: dict[str, Any] = Field(default_factory=dict)
    extraction_error: str | None = None


class FinalInvoiceJson(BaseModel):
    source_file: str
    header: dict[str, Any] = Field(default_factory=lambda: dict(HEADER_TEMPLATE))
    lineItems: dict[str, list[dict[str, Any]]] = Field(default_factory=lambda: {"lineItem": []})
    barcodeDetails: dict[str, Any] = Field(default_factory=dict)
    pageAudit: list[PageResult] = Field(default_factory=list)
    processing: dict[str, Any] = Field(default_factory=dict)


class AgentState(TypedDict, total=False):
    source_path: Path
    output_dir: Path
    work_dir: Path
    prompt: str
    page_images: list[Path]
    page_results: list[PageResult]
    final_json: FinalInvoiceJson
    output_json_path: Path
