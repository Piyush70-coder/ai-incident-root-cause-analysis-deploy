#!/usr/bin/env python3
"""
Standalone log collector agent for sending logs to the platform.

Example:
    python log_collector_agent.py --api_key=XXX --file=error.log
    python log_collector_agent.py --api_key=XXX --file=error.log --incident_id=<uuid>
"""

import argparse
import json
from pathlib import Path
from typing import Optional

import requests


INGEST_URL = "http://localhost:8000/api/v1/ingest/"
REQUEST_TIMEOUT_SECONDS = 15
MAX_LOG_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


def read_log_file_safely(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {file_path}")
    if path.stat().st_size == 0:
        raise ValueError("Log file is empty.")
    if path.stat().st_size > MAX_LOG_SIZE_BYTES:
        raise ValueError(
            f"Log file is too large ({path.stat().st_size} bytes). "
            f"Max supported: {MAX_LOG_SIZE_BYTES} bytes."
        )
    return path.read_text(encoding="utf-8", errors="replace")


def send_logs(
    api_key: str,
    file_path: str,
    incident_id: Optional[str] = None,
    endpoint: str = INGEST_URL,
) -> dict:
    log_text = read_log_file_safely(file_path)

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"log_text": log_text}
    if incident_id:
        payload["incident_id"] = incident_id

    try:
        response = requests.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"Connection error while sending logs: {exc}") from exc
    except requests.exceptions.Timeout as exc:
        raise RuntimeError("Request timed out while sending logs.") from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Request failed: {exc}") from exc

    try:
        response_data = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid response from server (status={response.status_code}): "
            f"{response.text[:300]}"
        ) from exc

    if response.status_code >= 400:
        raise RuntimeError(
            f"Server returned error status {response.status_code}: {response_data}"
        )

    if "status" not in response_data or "incident_id" not in response_data:
        raise RuntimeError(f"Unexpected response format: {response_data}")

    return response_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send logs to incident platform API")
    parser.add_argument("--api_key", required=True, help="Token API key")
    parser.add_argument("--file", required=True, help="Path to log file")
    parser.add_argument("--incident_id", help="Existing incident ID (optional)")
    parser.add_argument(
        "--endpoint",
        default=INGEST_URL,
        help=f"Ingestion URL (default: {INGEST_URL})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = send_logs(
            api_key=args.api_key,
            file_path=args.file,
            incident_id=args.incident_id,
            endpoint=args.endpoint,
        )
        print(f"Success: status={result['status']} incident_id={result['incident_id']}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
