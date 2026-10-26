from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from config import AppConfig
from oci_llm import (
    FatalLlmConfigurationError,
    _document_separator_prompt,
    _image_to_data_url,
    _normalize_separator_detection,
    _parse_json,
)


class GrokPageExtractor:
    """Use xAI's Responses API without requiring an additional SDK."""

    provider_name = "grok"

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.model_id = config.grok_model_id
        if not config.xai_api_key or config.xai_api_key.casefold() in {"replace_me", "your_xai_api_key"}:
            raise FatalLlmConfigurationError("XAI_API_KEY is required for Grok. Add your xAI API key to .env.")
        if not self.model_id:
            raise FatalLlmConfigurationError("GROK_MODEL_ID is required and must support image input.")
        url = urlsplit(config.grok_base_url)
        if url.scheme != "https" or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise FatalLlmConfigurationError("GROK_BASE_URL must be an HTTPS API base URL, such as https://api.x.ai/v1.")
        if config.grok_max_tokens <= 0 or config.grok_timeout_seconds <= 0:
            raise FatalLlmConfigurationError("GROK_MAX_TOKENS and GROK_TIMEOUT_SECONDS must be greater than zero.")
        if not 0 <= config.grok_temperature <= 2:
            raise FatalLlmConfigurationError("GROK_TEMPERATURE must be between 0 and 2.")
        if config.grok_reasoning_effort not in {"", "low", "medium", "high", "xhigh"}:
            raise FatalLlmConfigurationError("GROK_REASONING_EFFORT must be low, medium, high, xhigh, or blank.")

    def detect_document_separator_barcode(
        self, image_path: Path, page_number: int, local_barcode_detection: dict[str, Any]
    ) -> dict[str, Any]:
        raw = self._request_json(
            image_path=image_path,
            system_text="You identify document separator Code 39 barcode pages from page images. Return JSON only.",
            user_text=_document_separator_prompt(page_number, local_barcode_detection),
            max_tokens=min(2000, self.config.grok_max_tokens),
        )
        if not isinstance(raw.get("is_document_separator"), bool):
            raise ValueError("Grok separator response must contain a boolean is_document_separator.")
        return _normalize_separator_detection(raw, page_number, source="grok_llm_vision")

    def extract_page(
        self, image_path: Path, page_number: int, prompt: str, barcode_detection: dict[str, Any]
    ) -> dict[str, Any]:
        result = self._request_json(
            image_path=image_path,
            system_text="Return invoice extraction JSON only. Use JSON null for missing values. Do not include explanations.",
            user_text=(
                f"{prompt}\n\nPage number: {page_number}\n"
                f"Document separator barcode detection JSON:\n{json.dumps(barcode_detection, indent=2)}"
            ),
            max_tokens=self.config.grok_max_tokens,
        )
        lines = result.get("lineItems")
        if (
            not isinstance(result.get("header"), dict)
            or not isinstance(lines, dict)
            or not isinstance(lines.get("lineItem"), list)
            or any(not isinstance(item, dict) for item in lines["lineItem"])
        ):
            raise ValueError("Grok invoice response must contain header and a lineItems.lineItem array of objects.")
        return result

    def _request_json(
        self, *, image_path: Path, system_text: str, user_text: str, max_tokens: int
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_id,
            "input": [
                {"role": "system", "content": system_text},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_text},
                        {
                            "type": "input_image",
                            "image_url": _image_to_data_url(image_path, self.config.image_max_side),
                            "detail": "high",
                        },
                    ],
                },
            ],
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": max_tokens,
            "temperature": self.config.grok_temperature,
            "store": False,
            "stream": False,
        }
        if self.config.grok_reasoning_effort:
            payload["reasoning"] = {"effort": self.config.grok_reasoning_effort}
        request = Request(
            self.config.grok_base_url.rstrip("/") + "/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.xai_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Agentic-AP-Automation/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.grok_timeout_seconds) as response:
                result = json.load(response)
        except HTTPError as exc:
            # Never put raw request headers, credentials, or invoice payloads in errors.
            hint = {
                400: "Check GROK_MODEL_ID, image support, and generation settings.",
                401: "Check XAI_API_KEY in .env.",
                402: "Check your xAI API credit balance and billing.",
                403: "Check API key permissions, model access, and billing.",
                404: "Check GROK_MODEL_ID and GROK_BASE_URL.",
                422: "Check the model and generation settings.",
                429: "The xAI rate or usage limit was reached. Wait for a reset or check your account limits and credits.",
            }.get(exc.code, "Try again later or check xAI service status.")
            message = f"Grok API request failed (HTTP {exc.code}). {hint}"
            exc.close()
            if exc.code in {400, 401, 402, 403, 404, 422, 429}:
                raise FatalLlmConfigurationError(message) from exc
            raise RuntimeError(message) from exc
        except URLError as exc:
            raise FatalLlmConfigurationError(
                "Cannot connect to the Grok API. Check GROK_BASE_URL, network access, and proxy settings."
            ) from exc
        except TimeoutError as exc:
            raise TimeoutError("Grok request timed out. Check GROK_TIMEOUT_SECONDS before retrying.") from exc
        return _response_json(result)


def _response_json(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise ValueError("Grok returned an invalid response object.")
    if response.get("error"):
        raise ValueError("Grok returned an API error instead of an extraction result.")
    if response.get("status") != "completed":
        raise ValueError("Grok response did not complete. Check GROK_MAX_TOKENS before retrying.")
    parts: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal":
                raise ValueError("Grok declined to process this page.")
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])
    if not parts:
        raise ValueError("Grok response contained no extraction text.")
    return _parse_json("\n".join(parts))
