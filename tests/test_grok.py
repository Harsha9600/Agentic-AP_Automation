from __future__ import annotations

import base64
import io
import json
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from PIL import Image

import config as config_module
import grok_llm
from agent_workflow import AgenticAPInvoiceProcessor
from config import AppConfig
from grok_llm import GrokPageExtractor, _response_json
from oci_llm import FatalLlmConfigurationError, MockPageExtractor, OciPageExtractor, build_page_extractor


@pytest.fixture
def config(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Extract the invoice header and lineItems as JSON.", encoding="utf-8")
    return AppConfig(
        input_dir=tmp_path / "inputs",
        output_dir=tmp_path / "outputs",
        work_dir=tmp_path / "work",
        pdf_render_dpi=120,
        image_max_side=800,
        prompt_file=prompt,
        llm_provider="grok",
        oci_config_file=str(tmp_path / "missing-oci-config"),
        oci_profile="DEFAULT",
        oci_compartment_id="",
        oci_genai_endpoint="",
        oci_genai_model_id="saved-oci-model",
        oci_genai_max_tokens=4000,
        oci_genai_temperature=0,
        xai_api_key="test-secret-not-a-real-key",
    )


@pytest.fixture
def image_path(tmp_path):
    path = tmp_path / "invoice.png"
    Image.new("RGB", (48, 32), "white").save(path)
    return path


def api_response(payload, **overrides):
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(payload)}]},
        ],
        **overrides,
    }


def stub_responses(monkeypatch, payloads):
    calls = []
    responses = iter(payloads)

    def urlopen(request, timeout):
        calls.append((request, timeout))
        return io.BytesIO(json.dumps(next(responses)).encode("utf-8"))

    monkeypatch.setattr(grok_llm, "urlopen", urlopen)
    return calls


def test_env_defaults_and_oci_settings(monkeypatch):
    for name in list(config_module.os.environ):
        if name.startswith(("OCI_", "GROK_", "XAI_")) or name == "LLM_PROVIDER":
            monkeypatch.delenv(name)
    monkeypatch.setattr(config_module, "load_dotenv", lambda: None)
    monkeypatch.setenv("OCI_PROFILE", "SAVED_PROFILE")
    monkeypatch.setenv("OCI_GENAI_MODEL_ID", "saved-oci-model")
    config = AppConfig.from_env()
    assert config.llm_provider == "grok"
    assert config.grok_model_id == "grok-4.7"
    assert config.oci_profile == "SAVED_PROFILE"
    assert config.oci_genai_model_id == "saved-oci-model"
    monkeypatch.setenv("XAI_API_KEY", " secret ")
    monkeypatch.setenv("GROK_MODEL_ID", "other-image-model")
    config = AppConfig.from_env()
    assert config.xai_api_key == "secret"
    assert config.grok_model_id == "other-image-model"
    assert "secret" not in repr(config)


def test_provider_selection_keeps_oci_and_mock(config):
    assert isinstance(build_page_extractor(config), GrokPageExtractor)
    assert isinstance(build_page_extractor(replace(config, llm_provider="oci", xai_api_key="")), OciPageExtractor)
    assert isinstance(build_page_extractor(replace(config, llm_provider="mock", xai_api_key="")), MockPageExtractor)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"xai_api_key": ""}, "XAI_API_KEY"),
        ({"grok_model_id": ""}, "GROK_MODEL_ID"),
        ({"grok_base_url": "http://api.x.ai/v1"}, "HTTPS"),
        ({"grok_max_tokens": 0}, "greater than zero"),
        ({"grok_timeout_seconds": 0}, "greater than zero"),
        ({"grok_temperature": 3}, "GROK_TEMPERATURE"),
        ({"grok_reasoning_effort": "invalid"}, "GROK_REASONING_EFFORT"),
    ],
)
def test_invalid_config_fails_before_processing(config, changes, message):
    with pytest.raises(FatalLlmConfigurationError, match=message):
        AgenticAPInvoiceProcessor(replace(config, **changes))
    assert not config.output_dir.exists()


def test_grok_image_extraction_through_workflow(config, image_path, monkeypatch):
    separator = {
        "is_document_separator": False,
        "barcode_type": None,
        "barcode_value": None,
        "confidence": 0.99,
        "evidence": "Normal invoice page.",
    }
    invoice = {
        "header": {"INVOICE_NUMBER": "INV-42", "VENDOR_NAME": "Test Vendor", "INVOICE_AMOUNT": 120},
        "lineItems": {"lineItem": [{"LINE_DESCRIPTION": "Service", "LINE_AMOUNT": 100, "LINE_NUMBER": 8}]},
    }
    calls = stub_responses(monkeypatch, [api_response(separator), api_response(invoice)])
    result = AgenticAPInvoiceProcessor(config).run(image_path)
    assert result.header["INVOICE_NUMBER"] == "INV-42"
    assert result.lineItems["lineItem"][0]["LINE_NUMBER"] == 1
    assert result.processing["llm_provider"] == "grok"
    assert result.processing["model_id"] == config.grok_model_id
    assert result.pageAudit[0].extraction_error is None
    assert result.pageAudit[0].document_separator_detection["source"] == "grok_llm_vision"
    saved = json.loads((config.output_dir / "invoice_ap_extraction.json").read_text(encoding="utf-8"))
    assert saved["header"]["INVOICE_NUMBER"] == "INV-42"
    assert config.xai_api_key not in json.dumps(saved)
    assert len(calls) == 2
    for request, timeout in calls:
        assert request.full_url == "https://api.x.ai/v1/responses"
        assert request.get_method() == "POST"
        assert request.get_header("Authorization") == f"Bearer {config.xai_api_key}"
        assert timeout == config.grok_timeout_seconds
        payload = json.loads(request.data)
        assert payload["model"] == config.grok_model_id
        assert payload["store"] is False
        assert payload["stream"] is False
        assert payload["text"]["format"]["type"] == "json_object"
        assert payload["reasoning"] == {"effort": "low"}
        data_url = payload["input"][1]["content"][1]["image_url"]
        with Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))) as image:
            assert image.format == "JPEG"
    extraction_prompt = json.loads(calls[1][0].data)["input"][1]["content"][0]["text"]
    assert "grok_llm_vision" in extraction_prompt


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 422, 429])
def test_access_errors_abort_without_dump(config, image_path, monkeypatch, status):
    def fail(request, timeout):
        raise HTTPError(request.full_url, status, "request failed", {}, io.BytesIO(b"secret body"))

    monkeypatch.setattr(grok_llm, "urlopen", fail)
    with pytest.raises(FatalLlmConfigurationError, match=f"HTTP {status}") as error:
        AgenticAPInvoiceProcessor(config).run(image_path)
    assert config.xai_api_key not in str(error.value)
    assert "secret body" not in str(error.value)
    assert not list(config.output_dir.glob("*.json"))


def test_network_error_is_clear(config, image_path, monkeypatch):
    def fail(request, timeout):
        raise URLError("network unavailable")

    monkeypatch.setattr(grok_llm, "urlopen", fail)
    with pytest.raises(FatalLlmConfigurationError, match="Cannot connect"):
        GrokPageExtractor(config).extract_page(image_path, 1, "prompt", {})


def test_separator_rejects_qr(config, image_path, monkeypatch):
    calls = stub_responses(
        monkeypatch,
        [api_response({"is_document_separator": True, "barcode_type": "QR Code", "barcode_value": "payment"})],
    )
    detection = GrokPageExtractor(config).detect_document_separator_barcode(image_path, 2, {})
    assert detection["is_document_separator"] is False
    assert detection["barcode_value"] is None
    assert len(calls) == 1


def test_separator_records_code39(config, image_path, monkeypatch):
    stub_responses(
        monkeypatch,
        [api_response({"is_document_separator": True, "barcode_type": "Code 39", "barcode_value": " SEP-42 "})],
    )
    detection = GrokPageExtractor(config).detect_document_separator_barcode(image_path, 2, {})
    assert detection["is_document_separator"] is True
    assert detection["barcode_value"] == "SEP-42"
    assert detection["page_number"] == 2


def test_string_false_is_not_a_separator_decision(config, image_path, monkeypatch):
    stub_responses(monkeypatch, [api_response({"is_document_separator": "false"})])
    with pytest.raises(ValueError, match="boolean"):
        GrokPageExtractor(config).detect_document_separator_barcode(image_path, 1, {})


def test_invoice_rejects_incorrect_shape(config, image_path, monkeypatch):
    stub_responses(monkeypatch, [api_response({"invoice_number": "wrong-shape"})])
    with pytest.raises(ValueError, match="lineItems"):
        GrokPageExtractor(config).extract_page(image_path, 1, "prompt", {})


@pytest.mark.parametrize(
    "response, message",
    [
        (api_response({}, status="incomplete"), "did not complete"),
        ({"status": "completed", "output": []}, "no extraction text"),
        ({"status": "completed", "error": {"code": "failure"}}, "API error"),
        (api_response([]), "JSON object"),
        ({"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]}, "declined"),
    ],
)
def test_invalid_responses_not_accepted(response, message):
    with pytest.raises(ValueError, match=message):
        _response_json(response)
