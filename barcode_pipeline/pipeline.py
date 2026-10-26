from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Callable

from .local_barcode import DEFAULT_SEPARATOR_FORMATS, detect_barcodes_on_image
from .models import BarcodeDetection, InvoiceDocument, PipelineResult
from .pdf_utils import render_pdf_pages, write_pages

ProgressCallback = Callable[[int, int, Path], None]
LOGGER = logging.getLogger(__name__)


def process_pdf(
    pdf_path: Path,
    output_dir: Path,
    *,
    dpi: int = 300,
    separator_formats: tuple[str, ...] = DEFAULT_SEPARATOR_FORMATS,
    separator_value_prefixes: tuple[str, ...] = (),
    progress_callback: ProgressCallback | None = None,
) -> PipelineResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.info(
        "Starting PDF processing source=%s output_dir=%s dpi=%s separator_formats=%s separator_prefixes=%s",
        pdf_path,
        output_dir,
        dpi,
        separator_formats,
        separator_value_prefixes,
    )

    with tempfile.TemporaryDirectory(prefix="barcode_pages_") as temp_dir:
        image_paths = render_pdf_pages(pdf_path, Path(temp_dir), dpi=dpi)
        LOGGER.info("Rendered %s page image(s) from %s", len(image_paths), pdf_path.name)
        detections: list[BarcodeDetection] = []

        for index, image_path in enumerate(image_paths, start=1):
            if progress_callback:
                progress_callback(index, len(image_paths), image_path)

            detection = detect_barcodes_on_image(
                image_path,
                index,
                separator_formats=separator_formats,
                separator_value_prefixes=separator_value_prefixes,
            )
            detections.append(detection)

    barcode_pages = [d.page_number for d in detections if d.is_separator_page]
    LOGGER.info(
        "Barcode detection completed for %s: separator_pages=%s pages_with_any_barcode=%s",
        pdf_path.name,
        barcode_pages,
        [d.page_number for d in detections if d.has_barcode],
    )
    invoice_documents = split_invoices(pdf_path, output_dir, detections)
    LOGGER.info(
        "PDF processing completed for %s: invoice_count=%s",
        pdf_path.name,
        len(invoice_documents),
    )

    return PipelineResult(
        source_pdf=pdf_path,
        barcode_pages=barcode_pages,
        invoice_documents=invoice_documents,
        detections=detections,
    )


def split_invoices(pdf_path: Path, output_dir: Path, detections: list[BarcodeDetection]) -> list[InvoiceDocument]:
    barcode_detections = [d for d in detections if d.is_separator_page]
    invoice_documents: list[InvoiceDocument] = []
    if not barcode_detections:
        LOGGER.info("No separator barcode pages found in %s; split output will be empty", pdf_path.name)

    for index, detection in enumerate(barcode_detections, start=1):
        next_separator_page = (
            barcode_detections[index].page_number
            if index < len(barcode_detections)
            else len(detections) + 1
        )
        included_pages = list(range(detection.page_number + 1, next_separator_page))
        if not included_pages:
            LOGGER.info(
                "Skipping separator page %s in %s because no invoice pages followed before the next separator",
                detection.page_number,
                pdf_path.name,
            )
            continue

        barcode_value = _safe_filename(detection.barcodes[0].barcode_value if detection.barcodes else None)
        suffix = f"_{barcode_value}" if barcode_value else ""
        output_pdf = output_dir / f"{pdf_path.stem}_invoice_{index:03d}{suffix}.pdf"
        write_pages(pdf_path, output_pdf, included_pages)
        LOGGER.info(
            "Wrote invoice %s for %s from separator_page=%s included_pages=%s output=%s",
            index,
            pdf_path.name,
            detection.page_number,
            included_pages,
            output_pdf,
        )

        invoice_documents.append(
            InvoiceDocument(
                invoice_number=index,
                output_pdf=output_pdf,
                separator_page=detection.page_number,
                included_pages=included_pages,
                barcode=detection.barcodes[0] if detection.barcodes else None,
            )
        )

    return invoice_documents


def _safe_filename(value: str | None) -> str:
    if not value:
        return ""
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return safe[:80].strip("_")
