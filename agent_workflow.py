from __future__ import annotations

import json
import logging
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

from app_models import AgentState, FinalInvoiceJson, HEADER_TEMPLATE, LINE_TEMPLATE, PageResult
from barcode_pipeline.local_barcode import DEFAULT_SEPARATOR_FORMATS, detect_barcodes_on_image
from config import AppConfig
from document_converter import convert_source_to_images
from oci_llm import FatalLlmConfigurationError, PageExtractor, build_page_extractor

LOGGER = logging.getLogger(__name__)


class AgenticAPInvoiceProcessor:
    def __init__(self, config: AppConfig, page_extractor: PageExtractor | None = None) -> None:
        self.config = config
        self.page_extractor = page_extractor or build_page_extractor(config)
        self.graph = self._build_graph()

    def run(self, source_path: Path, output_dir: Path | None = None) -> FinalInvoiceJson:
        self.config.ensure_dirs()
        source_path = source_path.resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"Input file not found: {source_path}")

        run_id = f"{source_path.stem}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        state: AgentState = {
            "source_path": source_path,
            "output_dir": (output_dir or self.config.output_dir).resolve(),
            "work_dir": (self.config.work_dir / run_id).resolve(),
        }
        final_state = self.graph.invoke(state)
        return final_state["final_json"]

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("intake_agent", self._intake_agent)
        graph.add_node("prompt_agent", self._prompt_agent)
        graph.add_node("pdf_to_image_agent", self._pdf_to_image_agent)
        graph.add_node("page_extraction_agent", self._page_extraction_agent)
        graph.add_node("finalizer_agent", self._finalizer_agent)
        graph.add_node("local_dump_agent", self._local_dump_agent)

        graph.set_entry_point("intake_agent")
        graph.add_edge("intake_agent", "prompt_agent")
        graph.add_edge("prompt_agent", "pdf_to_image_agent")
        graph.add_edge("pdf_to_image_agent", "page_extraction_agent")
        graph.add_edge("page_extraction_agent", "finalizer_agent")
        graph.add_edge("finalizer_agent", "local_dump_agent")
        graph.add_edge("local_dump_agent", END)
        return graph.compile()

    def _intake_agent(self, state: AgentState) -> AgentState:
        state["work_dir"].mkdir(parents=True, exist_ok=True)
        state["output_dir"].mkdir(parents=True, exist_ok=True)
        LOGGER.info("Intake agent accepted %s", state["source_path"])
        return state

    def _prompt_agent(self, state: AgentState) -> AgentState:
        prompt_path = self.config.prompt_file
        if not prompt_path.is_absolute():
            prompt_path = Path.cwd() / prompt_path
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
        prompt = prompt_path.read_text(encoding="utf-8")
        LOGGER.info("Prompt agent loaded %s", prompt_path)
        return {**state, "prompt": prompt}

    def _pdf_to_image_agent(self, state: AgentState) -> AgentState:
        page_dir = state["work_dir"] / "pages"
        page_images = convert_source_to_images(
            state["source_path"],
            page_dir,
            dpi=self.config.pdf_render_dpi,
        )
        if not page_images:
            raise ValueError("No page images were produced from the uploaded file.")
        LOGGER.info("PDF-to-image agent produced %s page image(s).", len(page_images))
        return {**state, "page_images": page_images}

    def _page_extraction_agent(self, state: AgentState) -> AgentState:
        page_results: list[PageResult] = []
        for page_number, image_path in enumerate(state["page_images"], start=1):
            LOGGER.info("Page extraction agent started page %s/%s", page_number, len(state["page_images"]))
            barcode_detection = detect_barcodes_on_image(
                image_path,
                page_number,
                separator_formats=DEFAULT_SEPARATOR_FORMATS,
                separator_value_prefixes=(),
            )
            raw_barcode_json = barcode_detection.to_dict()
            try:
                separator_started = time.perf_counter()
                LOGGER.info(
                    "Page extraction agent asking %s LLM to identify document separator barcode on page %s.",
                    self.page_extractor.provider_name,
                    page_number,
                )
                separator_detection = self.page_extractor.detect_document_separator_barcode(
                    image_path=image_path,
                    page_number=page_number,
                    local_barcode_detection=raw_barcode_json,
                )
                LOGGER.info(
                    "Page extraction agent completed LLM separator check for page %s in %.1f seconds: is_separator=%s value=%s",
                    page_number,
                    time.perf_counter() - separator_started,
                    separator_detection.get("is_document_separator"),
                    separator_detection.get("barcode_value"),
                )
                llm_started = time.perf_counter()
                LOGGER.info("Page extraction agent sending page %s to %s LLM.", page_number, self.page_extractor.provider_name)
                llm_json = self.page_extractor.extract_page(
                    image_path=image_path,
                    page_number=page_number,
                    prompt=state["prompt"],
                    barcode_detection=separator_detection,
                )
                LOGGER.info(
                    "Page extraction agent completed LLM extraction for page %s in %.1f seconds.",
                    page_number,
                    time.perf_counter() - llm_started,
                )
                page_results.append(
                    PageResult(
                        page_number=page_number,
                        image_path=str(image_path),
                        barcode_detection=raw_barcode_json,
                        document_separator_detection=separator_detection,
                        llm_json=llm_json,
                    )
                )
            except FatalLlmConfigurationError:
                raise
            except Exception as exc:
                LOGGER.exception("Page %s extraction failed", page_number)
                page_results.append(
                    PageResult(
                        page_number=page_number,
                        image_path=str(image_path),
                        barcode_detection=raw_barcode_json,
                        document_separator_detection={},
                        llm_json={},
                        extraction_error=str(exc),
                    )
                )
        return {**state, "page_results": page_results}

    def _finalizer_agent(self, state: AgentState) -> AgentState:
        final_json = finalize_invoice_json(
            source_path=state["source_path"],
            page_results=state["page_results"],
            provider=self.page_extractor.provider_name,
            model_id=self.page_extractor.model_id,
        )
        LOGGER.info("Finalizer agent merged %s page result(s).", len(state["page_results"]))
        return {**state, "final_json": final_json}

    def _local_dump_agent(self, state: AgentState) -> AgentState:
        output_path = state["output_dir"] / f"{state['source_path'].stem}_ap_extraction.json"
        output_path.write_text(state["final_json"].model_dump_json(indent=2), encoding="utf-8")
        LOGGER.info("Local dump agent wrote %s", output_path)
        return {**state, "output_json_path": output_path}


def process_invoice(source_path: Path, output_dir: Path | None = None, config: AppConfig | None = None) -> FinalInvoiceJson:
    processor = AgenticAPInvoiceProcessor(config or AppConfig.from_env())
    return processor.run(source_path, output_dir)


def finalize_invoice_json(
    *,
    source_path: Path,
    page_results: list[PageResult],
    provider: str,
    model_id: str,
) -> FinalInvoiceJson:
    header = deepcopy(HEADER_TEMPLATE)
    line_items: list[dict[str, Any]] = []

    for page in page_results:
        page_header = page.llm_json.get("header", {})
        if isinstance(page_header, dict):
            for key in header:
                value = page_header.get(key)
                if _has_value(value) and _should_update_header_value(key, header.get(key)):
                    header[key] = value

        page_line_items = _extract_line_items(page.llm_json)
        for raw_item in page_line_items:
            item = deepcopy(LINE_TEMPLATE)
            for key in item:
                if key in raw_item:
                    item[key] = raw_item[key]
            item["LINE_NUMBER"] = len(line_items) + 1
            line_items.append(item)

    if _has_value(header.get("PO_NUMBER")):
        header["DOCUMENT_TYPE"] = "PO"
    elif not _has_value(header.get("DOCUMENT_TYPE")):
        header["DOCUMENT_TYPE"] = "NON-PO"
    if not _has_value(header.get("INVOICE_TYPE")):
        header["INVOICE_TYPE"] = "STANDARD"
    if not _has_value(header.get("LANGUAGE_CODE")):
        header["LANGUAGE_CODE"] = "UNK"

    separator_barcode_values: list[str] = []
    separator_barcode_pages: list[int] = []
    separator_barcode_records: list[dict[str, Any]] = []
    for page in page_results:
        detection = page.document_separator_detection
        if not detection.get("is_document_separator"):
            continue
        separator_barcode_pages.append(page.page_number)
        value = detection.get("barcode_value")
        if value and value not in separator_barcode_values:
            separator_barcode_values.append(value)
        separator_barcode_records.append(
            {
                "page_number": page.page_number,
                "barcode_type": detection.get("barcode_type") or "Code 39",
                "barcode_value": value,
                "confidence": detection.get("confidence"),
                "evidence": detection.get("evidence"),
                "source": detection.get("source", "llm_vision"),
            }
        )

    return FinalInvoiceJson(
        source_file=str(source_path),
        header=header,
        lineItems={"lineItem": line_items},
        barcodeDetails={
            "scope": "document_separator_code_39_only",
            "decision_source": "llm_vision",
            "has_barcode": bool(separator_barcode_records),
            "barcode_pages": separator_barcode_pages,
            "barcode_values": separator_barcode_values,
            "barcodes": separator_barcode_records,
        },
        pageAudit=page_results,
        processing={
            "processed_at_utc": datetime.now(UTC).isoformat(),
            "page_count": len(page_results),
            "all_pages_considered": len(page_results) > 0,
            "llm_provider": provider,
            "model_id": model_id,
            "local_dump_format": "json",
            "separator_barcode_decision_source": "llm_vision",
        },
    )


def _extract_line_items(llm_json: dict[str, Any]) -> list[dict[str, Any]]:
    container = llm_json.get("lineItems")
    if not isinstance(container, dict):
        return []
    items = container.get("lineItem")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and _line_has_content(item)]


def _line_has_content(item: dict[str, Any]) -> bool:
    return any(key != "LINE_NUMBER" and _has_value(value) for key, value in item.items())


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def _should_update_header_value(key: str, current_value: Any) -> bool:
    if not _has_value(current_value):
        return True
    return (
        key == "DOCUMENT_TYPE" and current_value == "NON-PO"
        or key == "INVOICE_TYPE" and current_value == "STANDARD"
        or key == "LANGUAGE_CODE" and current_value == "UNK"
    )

