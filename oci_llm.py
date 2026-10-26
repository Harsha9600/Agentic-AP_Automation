from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from config import AppConfig


class FatalLlmConfigurationError(RuntimeError):
    pass


class PageExtractor(Protocol):
    provider_name: str
    model_id: str

    def detect_document_separator_barcode(
        self,
        image_path: Path,
        page_number: int,
        local_barcode_detection: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def extract_page(self, image_path: Path, page_number: int, prompt: str, barcode_detection: dict[str, Any]) -> dict[str, Any]:
        ...


class MockPageExtractor:
    provider_name = "mock"
    model_id = "mock"

    def detect_document_separator_barcode(
        self,
        image_path: Path,
        page_number: int,
        local_barcode_detection: dict[str, Any],
    ) -> dict[str, Any]:
        barcodes = [
            barcode
            for barcode in local_barcode_detection.get("barcodes", [])
            if str(barcode.get("barcode_type") or "").strip().casefold() == "code 39"
        ]
        return {
            "page_number": page_number,
            "is_document_separator": bool(local_barcode_detection.get("is_separator_page") and barcodes),
            "barcode_type": "Code 39" if local_barcode_detection.get("is_separator_page") and barcodes else None,
            "barcode_value": barcodes[0].get("barcode_value") if local_barcode_detection.get("is_separator_page") and barcodes else None,
            "confidence": 1.0 if local_barcode_detection.get("is_separator_page") and barcodes else 0.0,
            "evidence": "Mock mode mirrored local MTI Code 39 separator detection.",
        }

    def extract_page(self, image_path: Path, page_number: int, prompt: str, barcode_detection: dict[str, Any]) -> dict[str, Any]:
        return {
            "header": {},
            "lineItems": {"lineItem": []},
            "_mock_note": "Mock provider used. Select grok or oci and configure its credentials for real extraction.",
        }


class OciPageExtractor:
    provider_name = "oci"

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.model_id = config.oci_genai_model_id
        self._client = None
        self._models = None

    def detect_document_separator_barcode(
        self,
        image_path: Path,
        page_number: int,
        local_barcode_detection: dict[str, Any],
    ) -> dict[str, Any]:
        client, models = self._client_and_models()
        prompt = _document_separator_prompt(page_number, local_barcode_detection)
        chat_details = self._chat_details(
            models=models,
            system_text="You identify document separator Code 39 barcode pages from page images.",
            user_text=prompt,
            image_path=image_path,
            max_tokens=700,
        )
        try:
            response = client.chat(chat_details)
        except Exception as exc:
            if getattr(exc, "status", None) in {401, 403, 404}:
                raise FatalLlmConfigurationError(_oci_setup_message(exc, self.config)) from exc
            raise
        return _normalize_separator_detection(_parse_json(_extract_text(response.data)), page_number)

    def extract_page(self, image_path: Path, page_number: int, prompt: str, barcode_detection: dict[str, Any]) -> dict[str, Any]:
        client, models = self._client_and_models()
        user_prompt = (
            f"{prompt}\n\n"
            f"Page number: {page_number}\n"
            f"Document separator barcode detection JSON:\n{json.dumps(barcode_detection, indent=2)}"
        )
        chat_details = self._chat_details(
            models=models,
            system_text="Return invoice extraction JSON only. Do not include Markdown or explanations.",
            user_text=user_prompt,
            image_path=image_path,
            max_tokens=self.config.oci_genai_max_tokens,
        )
        try:
            response = client.chat(chat_details)
        except Exception as exc:
            if getattr(exc, "status", None) in {401, 403, 404}:
                raise FatalLlmConfigurationError(_oci_setup_message(exc, self.config)) from exc
            raise
        return _parse_json(_extract_text(response.data))

    def _chat_details(self, *, models, system_text: str, user_text: str, image_path: Path, max_tokens: int):
        return models.ChatDetails(
            compartment_id=self.config.oci_compartment_id,
            serving_mode=models.OnDemandServingMode(
                serving_type=models.OnDemandServingMode.SERVING_TYPE_ON_DEMAND,
                model_id=self.config.oci_genai_model_id,
            ),
            chat_request=models.GenericChatRequest(
                api_format=models.GenericChatRequest.API_FORMAT_GENERIC,
                messages=[
                    models.SystemMessage(
                        role=models.SystemMessage.ROLE_SYSTEM,
                        content=[models.TextContent(type=models.TextContent.TYPE_TEXT, text=system_text)],
                    ),
                    models.UserMessage(
                        role=models.UserMessage.ROLE_USER,
                        content=[
                            models.TextContent(type=models.TextContent.TYPE_TEXT, text=user_text),
                            models.ImageContent(
                                type=models.ImageContent.TYPE_IMAGE,
                                image_url=models.ImageUrl(
                                    url=_image_to_data_url(image_path, self.config.image_max_side),
                                    detail=models.ImageUrl.DETAIL_HIGH,
                                ),
                            ),
                        ],
                    ),
                ],
                response_format=models.JsonObjectResponseFormat(
                    type=models.JsonObjectResponseFormat.TYPE_JSON_OBJECT,
                ),
                temperature=self.config.oci_genai_temperature,
                max_tokens=max_tokens,
            ),
        )

    def _client_and_models(self):
        if self._client is not None and self._models is not None:
            return self._client, self._models
        if not self.config.oci_compartment_id:
            raise FatalLlmConfigurationError("OCI_COMPARTMENT_ID is required.")
        if not self.config.oci_genai_endpoint:
            raise FatalLlmConfigurationError("OCI_GENAI_ENDPOINT is required.")
        if not self.config.oci_genai_model_id:
            raise FatalLlmConfigurationError("OCI_GENAI_MODEL_ID is required.")

        import oci
        from oci.generative_ai_inference import GenerativeAiInferenceClient, models

        client_config = oci.config.from_file(self.config.oci_config_file, self.config.oci_profile)
        oci.config.validate_config(client_config)
        key_file = Path(client_config["key_file"]).expanduser()
        if not key_file.exists():
            raise FatalLlmConfigurationError(f"OCI key_file does not exist: {key_file}")
        signer = oci.signer.Signer.from_config(client_config)

        self._client = GenerativeAiInferenceClient(
            client_config,
            signer=signer,
            service_endpoint=self.config.oci_genai_endpoint,
        )
        self._models = models
        return self._client, self._models


def build_page_extractor(config: AppConfig) -> PageExtractor:
    if config.llm_provider == "mock":
        return MockPageExtractor()
    if config.llm_provider == "oci":
        return OciPageExtractor(config)
    if config.llm_provider == "grok":
        from grok_llm import GrokPageExtractor

        return GrokPageExtractor(config)
    raise ValueError("LLM_PROVIDER must be 'grok', 'oci', or 'mock'.")


def _document_separator_prompt(page_number: int, local_barcode_detection: dict[str, Any]) -> str:
    return (
        "Analyze this page image and identify ONLY document separator barcode pages.\n"
        "A document separator page is a mostly standalone separator sheet that visually says or implies "
        "'document separator' and contains a Code 39 separator barcode. Ignore payment barcodes, QR codes, "
        "banking barcodes, and any barcodes printed at the bottom of normal invoice pages.\n\n"
        "Return only this JSON object, with no Markdown or explanation:\n"
        "{\n"
        f'  "page_number": {page_number},\n'
        '  "is_document_separator": true_or_false,\n'
        '  "barcode_type": "Code 39" or null,\n'
        '  "barcode_value": "decoded value if readable" or null,\n'
        '  "confidence": number_between_0_and_1,\n'
        '  "evidence": "short visual reason"\n'
        "}\n\n"
        "If the page is a normal invoice page with a barcode at the bottom, return "
        '"is_document_separator": false and null barcode fields.\n\n'
        f"Local raw barcode detector output for reference only:\n{json.dumps(local_barcode_detection, indent=2)}"
    )


def _image_to_data_url(image_path: Path, max_side: int) -> str:
    with Image.open(image_path) as image:
        image = image.convert("RGB")
        image.thumbnail((max_side, max_side))
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _extract_text(chat_result) -> str:
    response = getattr(chat_result, "chat_response", None)
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise ValueError("OCI response contained no choices.")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None) or []
    parts = [getattr(item, "text", "") for item in content if getattr(item, "text", "")]
    if not parts:
        raise ValueError("OCI response contained no text.")
    return "\n".join(parts)


def _parse_json(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object from the model.")
    return parsed


def _normalize_separator_detection(
    raw: dict[str, Any], page_number: int, *, source: str = "oci_llm_vision"
) -> dict[str, Any]:
    is_separator = bool(raw.get("is_document_separator"))
    barcode_type = raw.get("barcode_type") if is_separator else None
    barcode_value = raw.get("barcode_value") if is_separator else None
    if barcode_type and str(barcode_type).strip().casefold() != "code 39":
        is_separator = False
        barcode_type = None
        barcode_value = None
    return {
        "page_number": page_number,
        "is_document_separator": is_separator,
        "barcode_type": "Code 39" if is_separator and barcode_value else None,
        "barcode_value": str(barcode_value).strip() if is_separator and barcode_value else None,
        "confidence": _to_float(raw.get("confidence")),
        "evidence": raw.get("evidence") if isinstance(raw.get("evidence"), str) else None,
        "source": source,
    }


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _oci_setup_message(exc: Exception, config: AppConfig) -> str:
    return (
        "OCI Generative AI call failed before extraction could continue. "
        f"status={getattr(exc, 'status', 'unknown')} code={getattr(exc, 'code', 'unknown')} "
        f"message={getattr(exc, 'message', str(exc))}. "
        f"Endpoint={config.oci_genai_endpoint}. "
        "Verify OCI_COMPARTMENT_ID is the GenAI compartment OCID, the model ID exists in the same region, "
        "and the OCI config user/group has permission to use generative-ai-family."
    )
