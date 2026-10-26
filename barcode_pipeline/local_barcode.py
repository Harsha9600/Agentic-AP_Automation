from __future__ import annotations

import logging
import os
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps
import pytesseract
import zxingcpp

from .models import BarcodeDetail, BarcodeDetection


LOGGER = logging.getLogger(__name__)
_TESSERACT_UNAVAILABLE_LOGGED = False
_TESSERACT_CONFIGURED_CMD: str | None = None
_TESSERACT_STATUS_CACHE: dict[str, str | bool | None] | None = None
DEFAULT_SEPARATOR_FORMATS = ("Code 39",)
OCR_BLOCKED_WORDS = {
    "BATCH",
    "CHANGSHAN",
    "CHINA",
    "DATE",
    "DOCUMENT",
    "DUE",
    "INVOICES",
    "MINERALS",
    "PAGE",
    "PLACE",
    "SCANNING",
    "SEPARATOR",
    "SMICHINA",
    "TECHNOLOGIES",
    "THIS",
    "WHEN",
}


def detect_barcodes_on_image(
    image_path: Path,
    page_number: int,
    *,
    separator_formats: tuple[str, ...] = DEFAULT_SEPARATOR_FORMATS,
    separator_value_prefixes: tuple[str, ...] = (),
) -> BarcodeDetection:
    """Decode barcodes from a rendered PDF page image using local Python tools."""
    LOGGER.info("Page %s: starting local barcode detection for %s", page_number, image_path.name)
    with Image.open(image_path) as image:
        image.load()
        image = image.copy()
        results = _read_barcodes_with_retries(
            image,
            separator_formats,
            separator_value_prefixes,
            page_number=page_number,
        )

    barcodes = [_result_to_detail(result) for result in results if result.valid and result.text]
    if barcodes:
        LOGGER.info(
            "Page %s: zxing-cpp decoded %s barcode(s): %s",
            page_number,
            len(barcodes),
            _format_barcode_details(barcodes),
        )
    else:
        LOGGER.info("Page %s: zxing-cpp decoded no valid barcodes", page_number)

    looks_like_document_separator = _looks_like_document_separator(image)
    is_separator_page = _is_separator_detection(
        barcodes,
        separator_formats,
        separator_value_prefixes,
        looks_like_document_separator=looks_like_document_separator,
    )
    LOGGER.info(
        "Page %s: Code 39 separator check %s; visual_separator_candidate=%s",
        page_number,
        "passed" if is_separator_page else "failed",
        looks_like_document_separator,
    )

    evidence = f"Decoded {len(barcodes)} barcode(s) locally with zxing-cpp." if barcodes else None
    if not is_separator_page and looks_like_document_separator:
        LOGGER.info("Page %s: trying OCR fallback for printed separator text", page_number)
        barcode_value = _read_separator_human_text(image, page_number=page_number)
        if barcode_value:
            barcodes = [
                BarcodeDetail(
                    barcode_type="OCR",
                    barcode_value=barcode_value,
                    confidence=0.70,
                    position=None,
                )
            ]
            is_separator_page = _matches_separator_prefixes(barcode_value, separator_value_prefixes)
            evidence = "Identified separator barcode value from printed human-readable text."
            LOGGER.info(
                "Page %s: OCR fallback read value=%s; prefix separator check %s",
                page_number,
                barcode_value,
                "passed" if is_separator_page else "failed",
            )
        else:
            LOGGER.info("Page %s: OCR fallback did not find a separator value", page_number)

    LOGGER.info(
        "Page %s: final detection result has_barcode=%s is_separator_page=%s evidence=%s",
        page_number,
        bool(barcodes),
        is_separator_page,
        evidence,
    )

    return BarcodeDetection(
        page_number=page_number,
        has_barcode=bool(barcodes),
        is_separator_page=is_separator_page,
        barcodes=barcodes,
        evidence=evidence,
    )


def _read_barcodes_with_retries(
    image: Image.Image,
    separator_formats: tuple[str, ...],
    separator_value_prefixes: tuple[str, ...],
    *,
    page_number: int | None = None,
) -> list:
    all_results = _unique_results(zxingcpp.read_barcodes(image))
    LOGGER.debug(
        "Page %s: zxing-cpp original image decoded %s unique valid barcode(s)",
        page_number,
        len(all_results),
    )
    if _has_separator_result(all_results, separator_formats, separator_value_prefixes):
        LOGGER.debug("Page %s: separator barcode found on original image", page_number)
        return all_results

    for variant_index, variant in enumerate(_image_variants(image), start=1):
        variant_results = _unique_results(zxingcpp.read_barcodes(variant))
        all_results = _unique_results([*all_results, *variant_results])
        LOGGER.debug(
            "Page %s: zxing-cpp retry variant %s decoded %s barcode(s); total unique=%s",
            page_number,
            variant_index,
            len(variant_results),
            len(all_results),
        )
        if _has_separator_result(all_results, separator_formats, separator_value_prefixes):
            LOGGER.debug("Page %s: separator barcode found on retry variant %s", page_number, variant_index)
            return all_results

    return all_results


def _image_variants(image: Image.Image) -> list[Image.Image]:
    width, height = image.size
    grayscale = ImageOps.grayscale(image)
    contrast = ImageEnhance.Contrast(grayscale).enhance(3)

    return [
        grayscale,
        contrast,
        image.crop((0, int(height * 0.40), width, int(height * 0.85))),
        image.crop((int(width * 0.20), int(height * 0.20), int(width * 0.85), int(height * 0.85))),
        grayscale.crop((0, int(height * 0.40), width, int(height * 0.85))),
        contrast.crop((0, int(height * 0.40), width, int(height * 0.85))),
    ]


def _looks_like_document_separator(image: Image.Image) -> bool:
    grayscale = ImageOps.grayscale(image).resize((240, 320))
    width, height = grayscale.size
    aspect_ratio = image.width / image.height
    if not 0.70 <= aspect_ratio <= 0.85:
        return False

    dark_pixels = [pixel < 100 for pixel in grayscale.getdata()]
    dark_ratio = sum(dark_pixels) / len(dark_pixels)
    if not 0.06 <= dark_ratio <= 0.18:
        return False

    center = grayscale.crop((int(width * 0.20), int(height * 0.20), int(width * 0.85), int(height * 0.85)))
    center_dark = sum(pixel < 100 for pixel in center.getdata()) / (center.width * center.height)
    return center_dark >= 0.08


def _read_separator_human_text(image: Image.Image, *, page_number: int | None = None) -> str | None:
    status = get_ocr_runtime_status()
    if not status["available"]:
        _log_tesseract_unavailable(status["error"] or "Tesseract is unavailable.")
        return None

    for crop in _separator_text_crops(image):
        for variant in _ocr_image_variants(crop):
            for config in _ocr_configs():
                raw_text = _run_tesseract(variant, config)
                candidate = _best_ocr_candidate(raw_text)
                if candidate:
                    LOGGER.debug("Page %s: OCR candidate accepted: %s", page_number, candidate)
                    return candidate

    LOGGER.debug("Page %s: OCR returned no accepted candidate", page_number)
    return None


def _separator_text_crops(image: Image.Image) -> list[Image.Image]:
    width, height = image.size
    crop_boxes = [
        (0.40, 0.405, 0.72, 0.450),
        (0.35, 0.385, 0.75, 0.460),
        (0.34, 0.350, 0.75, 0.465),
        (0.25, 0.300, 0.80, 0.520),
        (0.32, 0.480, 0.75, 0.600),
    ]

    return [
        image.crop((int(width * left), int(height * top), int(width * right), int(height * bottom)))
        for left, top, right, bottom in crop_boxes
    ]


def _ocr_image_variants(image: Image.Image) -> list[Image.Image]:
    grayscale = ImageOps.grayscale(image)
    contrast = ImageEnhance.Contrast(grayscale).enhance(3)
    threshold = contrast.point(lambda pixel: 0 if pixel < 170 else 255)

    variants = []
    for source in (grayscale, contrast, threshold):
        for scale in (2, 3, 4):
            resized = source.resize((source.width * scale, source.height * scale))
            variants.append(ImageOps.expand(resized, border=30, fill=255))

    return variants


def _ocr_configs() -> tuple[str, ...]:
    whitelist = "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return (
        f"--psm 7 {whitelist}",
        f"--psm 8 {whitelist}",
        f"--psm 6 {whitelist}",
        f"--psm 13 {whitelist}",
    )


def _run_tesseract(image: Image.Image, config: str) -> str:
    configure_tesseract_from_env()
    try:
        return pytesseract.image_to_string(image, config=config)
    except (pytesseract.TesseractNotFoundError, FileNotFoundError) as exc:
        _log_tesseract_unavailable(exc)
        return ""
    except pytesseract.TesseractError as exc:
        LOGGER.debug("Tesseract OCR attempt failed: %s", exc)
        return ""


def configure_tesseract_from_env() -> str:
    """Apply TESSERACT_CMD after .env has been loaded by the entrypoint."""
    global _TESSERACT_CONFIGURED_CMD, _TESSERACT_STATUS_CACHE, _TESSERACT_UNAVAILABLE_LOGGED

    configured_cmd = os.getenv("TESSERACT_CMD", "").strip() or "tesseract"
    if _TESSERACT_CONFIGURED_CMD != configured_cmd:
        pytesseract.pytesseract.tesseract_cmd = configured_cmd
        _TESSERACT_CONFIGURED_CMD = configured_cmd
        _TESSERACT_STATUS_CACHE = None
        _TESSERACT_UNAVAILABLE_LOGGED = False

    return pytesseract.pytesseract.tesseract_cmd


def get_ocr_runtime_status(*, refresh: bool = False) -> dict[str, str | bool | None]:
    """Return OCR dependency status for startup logs and health diagnostics."""
    global _TESSERACT_STATUS_CACHE

    tesseract_cmd = configure_tesseract_from_env()
    if _TESSERACT_STATUS_CACHE is not None and not refresh:
        return dict(_TESSERACT_STATUS_CACHE)

    try:
        version = str(pytesseract.get_tesseract_version()).splitlines()[0]
    except (pytesseract.TesseractNotFoundError, FileNotFoundError) as exc:
        _TESSERACT_STATUS_CACHE = {
            "available": False,
            "tesseract_cmd": tesseract_cmd,
            "version": None,
            "error": str(exc),
        }
        return dict(_TESSERACT_STATUS_CACHE)
    except pytesseract.TesseractError as exc:
        _TESSERACT_STATUS_CACHE = {
            "available": False,
            "tesseract_cmd": tesseract_cmd,
            "version": None,
            "error": str(exc),
        }
        return dict(_TESSERACT_STATUS_CACHE)

    _TESSERACT_STATUS_CACHE = {
        "available": True,
        "tesseract_cmd": tesseract_cmd,
        "version": version,
        "error": None,
    }
    return dict(_TESSERACT_STATUS_CACHE)


def log_ocr_runtime_status(logger: logging.Logger | None = None) -> dict[str, str | bool | None]:
    """Log OCR dependency readiness once at process startup."""
    target_logger = logger or LOGGER
    status = get_ocr_runtime_status(refresh=True)
    if status["available"]:
        target_logger.info(
            "OCR fallback available: tesseract_cmd=%s version=%s",
            status["tesseract_cmd"],
            status["version"],
        )
    else:
        target_logger.warning(
            "OCR fallback unavailable: tesseract_cmd=%s error=%s",
            status["tesseract_cmd"],
            status["error"],
        )
    return status


def _log_tesseract_unavailable(exc: Exception | str) -> None:
    global _TESSERACT_UNAVAILABLE_LOGGED
    if _TESSERACT_UNAVAILABLE_LOGGED:
        return

    _TESSERACT_UNAVAILABLE_LOGGED = True
    LOGGER.warning(
        "OCR fallback is unavailable because the Tesseract executable is not installed or is not on PATH: %s",
        exc,
    )


def _best_ocr_candidate(raw_text: str) -> str | None:
    candidates = []
    for line in raw_text.splitlines():
        candidate = _clean_ocr_candidate(line)
        if candidate:
            candidates.append(candidate)
        for token in line.split():
            candidate = _clean_ocr_candidate(token)
            if candidate:
                candidates.append(candidate)

    return candidates[0] if candidates else None


def _clean_ocr_candidate(value: str) -> str | None:
    candidate = "".join(char for char in value.upper() if char.isalnum())
    if not 3 <= len(candidate) <= 32:
        return None
    if candidate in OCR_BLOCKED_WORDS:
        return None
    return candidate


def _unique_results(results: list) -> list:
    unique = {}
    for result in results:
        if not result.valid or not result.text:
            continue
        key = (str(result.format), result.text)
        unique.setdefault(key, result)
    return list(unique.values())


def _has_separator_result(
    results: list,
    separator_formats: tuple[str, ...],
    separator_value_prefixes: tuple[str, ...],
) -> bool:
    return any(
        _is_separator_barcode(_result_to_detail(result), separator_formats, separator_value_prefixes)
        for result in results
        if result.valid and result.text
    )


def _result_to_detail(result) -> BarcodeDetail:
    return BarcodeDetail(
        barcode_type=str(result.format) if result.format else None,
        barcode_value=result.text or None,
        confidence=1.0 if result.valid else None,
        position=str(result.position) if result.position else None,
    )


def _is_separator_detection(
    barcodes: list[BarcodeDetail],
    separator_formats: tuple[str, ...],
    separator_value_prefixes: tuple[str, ...],
    *,
    looks_like_document_separator: bool,
) -> bool:
    return any(
        _is_separator_barcode(
            barcode,
            separator_formats,
            separator_value_prefixes,
            looks_like_document_separator=looks_like_document_separator,
        )
        for barcode in barcodes
    )


def _is_separator_barcode(
    barcode: BarcodeDetail,
    separator_formats: tuple[str, ...],
    separator_value_prefixes: tuple[str, ...],
    *,
    looks_like_document_separator: bool = False,
) -> bool:
    barcode_type = (barcode.barcode_type or "").strip().casefold()
    allowed_formats = {value.strip().casefold() for value in separator_formats if value.strip()}
    if allowed_formats and barcode_type not in allowed_formats:
        return False
    if barcode_type != "code 39":
        return False

    prefixes = tuple(value.strip().casefold() for value in separator_value_prefixes if value.strip())
    barcode_value = (barcode.barcode_value or "").strip().casefold()
    if prefixes:
        return barcode_value.startswith(prefixes)

    return True


def _matches_separator_prefixes(barcode_value: str | None, separator_value_prefixes: tuple[str, ...]) -> bool:
    prefixes = tuple(value.strip().casefold() for value in separator_value_prefixes if value.strip())
    if not prefixes:
        return True

    value = (barcode_value or "").strip().casefold()
    return value.startswith(prefixes)


def _format_barcode_details(barcodes: list[BarcodeDetail]) -> str:
    details = []
    for barcode in barcodes:
        details.append(
            "type={type} value={value}".format(
                type=barcode.barcode_type or "unknown",
                value=barcode.barcode_value or "",
            )
        )
    return "; ".join(details)
