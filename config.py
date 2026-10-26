from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value and value.strip() else default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value and value.strip() else default


@dataclass(frozen=True)
class AppConfig:
    input_dir: Path
    output_dir: Path
    work_dir: Path
    pdf_render_dpi: int
    image_max_side: int
    prompt_file: Path
    llm_provider: str
    oci_config_file: str
    oci_profile: str
    oci_compartment_id: str
    oci_genai_endpoint: str
    oci_genai_model_id: str
    oci_genai_max_tokens: int
    oci_genai_temperature: float
    xai_api_key: str = field(default="", repr=False)
    grok_base_url: str = "https://api.x.ai/v1"
    grok_model_id: str = "grok-4.7"
    grok_max_tokens: int = 8000
    grok_temperature: float = 0.0
    grok_timeout_seconds: int = 180
    grok_reasoning_effort: str = "low"

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()
        return cls(
            input_dir=Path(os.getenv("APP_INPUT_DIR", "inputs")),
            output_dir=Path(os.getenv("APP_OUTPUT_DIR", "outputs")),
            work_dir=Path(os.getenv("APP_WORK_DIR", ".work")),
            pdf_render_dpi=_int_env("PDF_RENDER_DPI", 300),
            image_max_side=_int_env("IMAGE_MAX_SIDE", 1800),
            prompt_file=Path(os.getenv("PROMPT_FILE", "prompts/invoice_extraction_prompt.md")),
            llm_provider=os.getenv("LLM_PROVIDER", "grok").strip().lower(),
            oci_config_file=os.getenv("OCI_CONFIG_FILE", str(Path.home() / ".oci" / "config")),
            oci_profile=os.getenv("OCI_PROFILE", "DEFAULT"),
            oci_compartment_id=os.getenv("OCI_COMPARTMENT_ID", "").strip(),
            oci_genai_endpoint=os.getenv("OCI_GENAI_ENDPOINT", "").strip(),
            oci_genai_model_id=os.getenv("OCI_GENAI_MODEL_ID", "").strip(),
            oci_genai_max_tokens=_int_env("OCI_GENAI_MAX_TOKENS", 4000),
            oci_genai_temperature=_float_env("OCI_GENAI_TEMPERATURE", 0.0),
            xai_api_key=os.getenv("XAI_API_KEY", "").strip(),
            grok_base_url=os.getenv("GROK_BASE_URL", "https://api.x.ai/v1").strip().rstrip("/"),
            grok_model_id=os.getenv("GROK_MODEL_ID", "grok-4.7").strip(),
            grok_max_tokens=_int_env("GROK_MAX_TOKENS", 8000),
            grok_temperature=_float_env("GROK_TEMPERATURE", 0.0),
            grok_timeout_seconds=_int_env("GROK_TIMEOUT_SECONDS", 180),
            grok_reasoning_effort=os.getenv("GROK_REASONING_EFFORT", "low").strip().lower(),
        )

    def ensure_dirs(self) -> None:
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
