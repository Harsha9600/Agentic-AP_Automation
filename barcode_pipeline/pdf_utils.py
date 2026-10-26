from __future__ import annotations

from pathlib import Path

import fitz
from pypdf import PdfReader, PdfWriter


def render_pdf_pages(pdf_path: Path, output_dir: Path, dpi: int = 180) -> list[Path]:
    """Render every page in a PDF as a PNG image."""
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)

    with fitz.open(pdf_path) as document:
        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image_path = output_dir / f"page_{page_index + 1:03d}.png"
            pixmap.save(image_path)
            rendered.append(image_path)

    return rendered


def remove_pages(pdf_path: Path, output_path: Path, page_numbers: list[int]) -> Path:
    """Write a copy of pdf_path without the 1-based page_numbers."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pages_to_remove = set(page_numbers)

    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()

    for zero_based_index, page in enumerate(reader.pages):
        page_number = zero_based_index + 1
        if page_number not in pages_to_remove:
            writer.add_page(page)

    with output_path.open("wb") as handle:
        writer.write(handle)

    return output_path


def write_pages(pdf_path: Path, output_path: Path, page_numbers: list[int]) -> Path:
    """Write a new PDF containing the selected 1-based page_numbers."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()

    for page_number in page_numbers:
        if page_number < 1 or page_number > len(reader.pages):
            raise ValueError(f"Page number {page_number} is outside the PDF page range.")
        writer.add_page(reader.pages[page_number - 1])

    with output_path.open("wb") as handle:
        writer.write(handle)

    return output_path
