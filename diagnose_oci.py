from __future__ import annotations

from pathlib import Path

import oci

from config import AppConfig


def main() -> None:
    config = AppConfig.from_env()
    print("OCI diagnostic")
    print(f"  config_file_exists: {Path(config.oci_config_file).exists()}")
    print(f"  profile: {config.oci_profile}")
    print(f"  endpoint: {config.oci_genai_endpoint}")
    print(f"  compartment_type: {_ocid_type(config.oci_compartment_id)}")
    print(f"  model_id_type: {_ocid_type(config.oci_genai_model_id)}")

    client_config = oci.config.from_file(config.oci_config_file, config.oci_profile)
    oci.config.validate_config(client_config)
    key_file = Path(client_config["key_file"]).expanduser()
    print(f"  profile_region: {client_config.get('region')}")
    print(f"  key_file_exists: {key_file.exists()}")

    signer = oci.signer.Signer.from_config(client_config)
    print("  api_key_signer: ok")

    control_endpoint = config.oci_genai_endpoint.replace("inference.generativeai", "generativeai")
    client = oci.generative_ai.GenerativeAiClient(
        client_config,
        signer=signer,
        service_endpoint=control_endpoint,
    )
    try:
        response = client.list_models(compartment_id=config.oci_compartment_id, limit=10)
    except Exception as exc:
        print("  list_models: failed")
        print(f"  error_status: {getattr(exc, 'status', 'unknown')}")
        print(f"  error_code: {getattr(exc, 'code', 'unknown')}")
        print(f"  error_message: {getattr(exc, 'message', str(exc))}")
        print("  next_step: fix OCI_COMPARTMENT_ID, model access, region, or IAM policy before extraction.")
        return

    print(f"  list_models: ok ({len(response.data.items)} returned)")
    for item in response.data.items:
        print(f"  model: {getattr(item, 'display_name', None)} | {getattr(item, 'id', None)}")


def _ocid_type(value: str) -> str:
    parts = value.split(".")
    if len(parts) >= 2 and parts[0] == "ocid1":
        return parts[1]
    return "missing" if not value else "name"


if __name__ == "__main__":
    main()
