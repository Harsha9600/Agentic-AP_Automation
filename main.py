from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from agent_workflow import process_invoice
from config import AppConfig
from oci_llm import FatalLlmConfigurationError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run agentic AP invoice extraction.")
    parser.add_argument("source", type=Path, help="Invoice PDF or image.")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--provider", choices=["grok", "oci", "mock"], default=None)
    args = parser.parse_args()

    config = AppConfig.from_env()
    if args.provider:
        config = AppConfig(**{**config.__dict__, "llm_provider": args.provider})

    try:
        result = process_invoice(args.source, args.output_dir, config)
    except FatalLlmConfigurationError as exc:
        print(f"{config.llm_provider.upper()} setup or access error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    output_dir = (args.output_dir or config.output_dir).resolve()
    print(f"Wrote: {output_dir / f'{args.source.stem}_ap_extraction.json'}")
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
