from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BarcodeDetail:
    barcode_type: str | None = None
    barcode_value: str | None = None
    confidence: float | None = None
    position: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BarcodeDetection:
    page_number: int
    has_barcode: bool
    is_separator_page: bool = False
    barcodes: list[BarcodeDetail] = field(default_factory=list)
    evidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "has_barcode": self.has_barcode,
            "is_separator_page": self.is_separator_page,
            "barcodes": [barcode.to_dict() for barcode in self.barcodes],
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class InvoiceDocument:
    invoice_number: int
    output_pdf: Path
    separator_page: int
    included_pages: list[int]
    barcode: BarcodeDetail | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "invoice_number": self.invoice_number,
            "output_pdf": str(self.output_pdf),
            "separator_page": self.separator_page,
            "included_pages": self.included_pages,
            "barcode": self.barcode.to_dict() if self.barcode else None,
        }


@dataclass(frozen=True)
class PipelineResult:
    source_pdf: Path
    barcode_pages: list[int]
    invoice_documents: list[InvoiceDocument] = field(default_factory=list)
    detections: list[BarcodeDetection] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_pdf": str(self.source_pdf),
            "separator_pages": self.barcode_pages,
            "barcode_pages": self.barcode_pages,
            "invoice_documents": [invoice.to_dict() for invoice in self.invoice_documents],
            "detections": [d.to_dict() for d in self.detections],
        }
