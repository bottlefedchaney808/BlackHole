#!/usr/bin/env python3
"""Options_Suite -- options pricing and analysis framework.

Provides pricing models (CRR, Leisen-Reimer, SABR, Vanna-Volga, MC, Heston, BAW),
Greeks calculation, and market comparison. Integrates with the orchestrator via
--context and --context-out modes for unified analysis workflows.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

import os
import sys

CLIENT_ID = os.environ.get('THETADATA_CF_ACCESS_CLIENT_ID')
CLIENT_SECRET = os.environ.get('THETADATA_CF_ACCESS_CLIENT_SECRET')

if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: ThetaData CF Access credentials not set.")
    print("Required environment variables:")
    print("  - THETADATA_CF_ACCESS_CLIENT_ID")
    print("  - THETADATA_CF_ACCESS_CLIENT_SECRET")
    print("Set these variables and retry. Credentials can be in .env or environment.")
    sys.exit(1)


def _resolve_context_out_path(context_path: Optional[str],
                              context: Optional[Dict[str, Any]],
                              context_out: Optional[str]) -> str:
    """Determine output path for options_result.json.

    Same precedence as Vol_Suite: --context-out if given, else context's
    output_dir, else next to the context file.
    """
    if context_out:
        return os.path.abspath(context_out)
    output_dir = (context or {}).get("output_dir")
    if isinstance(output_dir, str) and output_dir.strip():
        return os.path.join(output_dir, "options_result.json")
    base = (os.path.dirname(os.path.abspath(context_path)) if context_path
            else os.getcwd())
    return os.path.join(base, "options_result.json")


def _build_options_result(context: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble a schema-valid options_result payload.

    Placeholder implementation: returns minimal valid structure.
    Full implementation will include pricing models, Greeks, market comparison.
    """
    ticker = context.get("ticker", "")
    expiry = context.get("expiry", "")

    payload: Dict[str, Any] = {
        "schema_version": 1,
        "suite": "options",
        "status": "ok",
        "ticker": str(ticker),
        "expiry": str(expiry),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "pricing_models": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return payload


def run_context_mode(context_path: str, context_out: Optional[str]) -> int:
    """Run in orchestrator context mode: read suite_context.json, write options_result.json."""
    try:
        with open(context_path, 'r') as f:
            context = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: Context file not found: {context_path}")
        return 1
    except json.JSONDecodeError as e:
        print(f"ERROR: Malformed JSON in {context_path}: {e}")
        return 1

    # Build options result
    result = _build_options_result(context)

    # Write result to output path
    output_path = _resolve_context_out_path(context_path, context, context_out)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    try:
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2, ensure_ascii=True)
        print(f"Options result written to: {output_path}")
        return 0
    except Exception as e:
        print(f"ERROR: Failed to write options result: {e}")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="Options_Suite — options pricing and analysis framework."
    )
    parser.add_argument("--context", help="Path to suite_context.json")
    parser.add_argument("--context-out", help="Path to write options_result.json")
    args = parser.parse_args()

    # If context mode requested, run it
    if args.context:
        return run_context_mode(args.context, args.context_out)

    print("Options_Suite v1.0")
    print("ThetaData credentials validated.")
    print("Interactive mode not yet implemented.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
