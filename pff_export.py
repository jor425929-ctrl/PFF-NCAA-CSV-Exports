#!/usr/bin/env python3
"""Check PFF API entitlement and save configured CSV exports."""

import os
import sys
from pathlib import Path
from typing import Any

import requests

API_BASE_URL = "https://api.pff.com"
WHOAMI_URL = f"{API_BASE_URL}/v1/auth/whoami"
EXPORTS_DIR = Path(__file__).parent / "exports"
TIMEOUT_SECONDS = 30

# Add the PFF report endpoint and parameters for each export you want.
# PFF v1 CSV requests use export=true. Replace each placeholder path with a
# real endpoint from the PFF API reference before uncommenting the request.
EXPORT_REQUESTS: list[dict[str, Any]] = [
    # {
    #     "filename": "ncaa_2024.csv",
    #     "endpoint": "/v1/your-ncaa-report-endpoint",
    #     "params": {"league": "ncaa", "season": 2024, "export": "true"},
    # },
    # {
    #     "filename": "ncaa_2025.csv",
    #     "endpoint": "/v1/your-ncaa-report-endpoint",
    #     "params": {"league": "ncaa", "season": 2025, "export": "true"},
    # },
]


def get_api_key() -> str:
    api_key = os.environ.get("PFF_API_KEY")
    if not api_key:
        raise RuntimeError(
            "PFF_API_KEY is not set. Add it as a Replit Secret or environment variable."
        )
    return api_key


def check_entitlement(session: requests.Session) -> bool:
    response = session.get(WHOAMI_URL, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict) or type(payload.get("entitled")) is not bool:
        raise ValueError(
            "The whoami response did not contain a boolean 'entitled' field."
        )

    entitled = payload["entitled"]
    print(f"PFF account entitled: {'yes' if entitled else 'no'}")
    return entitled


def save_csv(
    session: requests.Session,
    filename: str,
    endpoint: str,
    params: dict[str, Any],
) -> Path:
    if Path(filename).name != filename or not filename.lower().endswith(".csv"):
        raise ValueError(f"Export filename must be a simple .csv filename: {filename}")
    if not endpoint.startswith("/"):
        raise ValueError(f"Export endpoint must start with '/': {endpoint}")

    response = session.get(
        f"{API_BASE_URL}{endpoint}",
        params=params,
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = EXPORTS_DIR / filename
    output_path.write_bytes(response.content)
    return output_path


def main() -> int:
    try:
        api_key = get_api_key()
        with requests.Session() as session:
            session.headers.update({"Authorization": f"Bearer {api_key}"})

            if not check_entitlement(session):
                print("Skipping CSV exports because the account is not entitled.")
                return 0

            if not EXPORT_REQUESTS:
                print(
                    "No CSV exports configured yet. Add the NCAA 2024 and 2025 "
                    "requests to EXPORT_REQUESTS."
                )
                return 0

            for export in EXPORT_REQUESTS:
                path = save_csv(
                    session,
                    filename=export["filename"],
                    endpoint=export["endpoint"],
                    params=export["params"],
                )
                print(f"Saved {path.relative_to(Path(__file__).parent)}")
    except requests.RequestException as exc:
        print(f"PFF API request failed: {exc}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())