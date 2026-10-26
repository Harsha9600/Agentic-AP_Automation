from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

from barcode_pipeline.pdf_utils import render_pdf_pages

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def convert_source_to_images(source_path: Path, output_dir: Path, *, dpi: int) -> list[Path]:
    suffix = source_path.suffix.lower()
    output_dir.mkdir(parents=True, exist_ok=True)
    if suffix == ".pdf":
        return render_pdf_pages(source_path, output_dir, dpi=dpi)
    if suffix in IMAGE_SUFFIXES:
        return [_copy_image_as_page(source_path, output_dir)]
    raise ValueError(f"Unsupported file type: {source_path.suffix}. Upload a PDF or image.")


def _copy_image_as_page(source_path: Path, output_dir: Path) -> Path:
    target = output_dir / "page_001.png"
    if source_path.suffix.lower() == ".png":
        shutil.copyfile(source_path, target)
        return target
    with Image.open(source_path) as image:
        image.convert("RGB").save(target)
    return target
