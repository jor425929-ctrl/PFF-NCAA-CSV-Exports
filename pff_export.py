#!/usr/bin/env python3
"""Plan or download week-by-week PFF NCAA player-report exports."""

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
from pathlib import Path
import sys
import time
from typing import Any
import zipfile

import requests

API_BASE_URL = "https://api.pff.com"
WHOAMI_URL = f"{API_BASE_URL}/v1/auth/whoami"
LEAGUES_URL = f"{API_BASE_URL}/v1/leagues"
EXPORTS_DIR = Path(__file__).parent / "exports"
ZIP_PATH = Path(__file__).parent / "PFF_NCAA_2024_2025_WEEKLY_EXPORTS.zip"

SEASONS = (2024, 2025)
REPORTS = ("offense", "defense", "special-teams")
WEEK_GROUP = "REGPO"
TIMEOUT_SECONDS = 60
MAX_RETRIES = 5
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

# PFF documents a 20-export-per-minute account limit. Five seconds between
# export attempts keeps this script to at most 12 per minute.
SECONDS_BETWEEN_EXPORTS = 5.0


class ExportRateLimiter:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self.last_request_at: float | None = None

    def wait(self) -> None:
        now = time.monotonic()
        if self.last_request_at is not None:
            remaining = self.interval_seconds - (now - self.last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request_at = time.monotonic()


def get_api_key() -> str:
    api_key = os.environ.get("PFF_API_KEY")
    if not api_key:
        raise RuntimeError(
            "PFF_API_KEY is not set. Add it as a Replit Secret or environment variable."
        )
    return api_key


def retry_delay(response: requests.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return max(
                        0.0,
                        (retry_at - datetime.now(timezone.utc)).total_seconds(),
                    )
                except (TypeError, ValueError, OverflowError):
                    pass
    return min(2.0 ** attempt, 60.0)


def get_with_retries(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    is_export: bool = False,
    rate_limiter: ExportRateLimiter | None = None,
) -> requests.Response:
    for attempt in range(MAX_RETRIES + 1):
        if is_export and rate_limiter is not None:
            rate_limiter.wait()

        try:
            response = session.get(url, params=params, timeout=TIMEOUT_SECONDS)
        except (requests.Timeout, requests.ConnectionError):
            if attempt == MAX_RETRIES:
                raise
            delay = retry_delay(None, attempt)
            print(
                f"Temporary network failure; retrying in {delay:.0f}s "
                f"({attempt + 1}/{MAX_RETRIES})."
            )
            time.sleep(delay)
            continue

        if response.status_code in RETRYABLE_STATUS_CODES:
            if attempt == MAX_RETRIES:
                response.raise_for_status()
            delay = retry_delay(response, attempt)
            print(
                f"Temporary HTTP {response.status_code}; retrying in {delay:.0f}s "
                f"({attempt + 1}/{MAX_RETRIES})."
            )
            response.close()
            time.sleep(delay)
            continue

        response.raise_for_status()
        return response

    raise RuntimeError("Request ended without a response.")


def check_entitlement(session: requests.Session) -> bool:
    response = get_with_retries(session, WHOAMI_URL)
    payload = response.json()
    if not isinstance(payload, dict) or type(payload.get("entitled")) is not bool:
        raise ValueError(
            "The whoami response did not contain a boolean 'entitled' field."
        )

    entitled = payload["entitled"]
    print(f"PFF account entitled: {'yes' if entitled else 'no'}")
    return entitled


def get_regpo_weeks(session: requests.Session) -> list[int]:
    response = get_with_retries(session, LEAGUES_URL)
    payload = response.json()
    leagues = payload.get("leagues") if isinstance(payload, dict) else None
    if not isinstance(leagues, list):
        raise ValueError("The PFF leagues response did not contain a leagues list.")

    ncaa = next(
        (league for league in leagues if isinstance(league, dict) and league.get("slug") == "ncaa"),
        None,
    )
    if ncaa is None:
        raise ValueError("PFF did not return NCAA league metadata.")

    available_seasons = ncaa.get("seasons")
    if not isinstance(available_seasons, list):
        raise ValueError("PFF NCAA metadata did not list available seasons.")
    missing_seasons = [season for season in SEASONS if season not in available_seasons]
    if missing_seasons:
        raise ValueError(
            "PFF NCAA metadata does not list requested season(s): "
            + ", ".join(map(str, missing_seasons))
        )

    week_groups = ncaa.get("week_groups")
    if not isinstance(week_groups, list):
        raise ValueError("PFF NCAA metadata did not list week groups.")
    regpo = next(
        (
            group
            for group in week_groups
            if isinstance(group, dict) and group.get("value") == WEEK_GROUP
        ),
        None,
    )
    if regpo is None or not isinstance(regpo.get("weeks"), list):
        raise ValueError(f"PFF NCAA metadata did not list weeks for {WEEK_GROUP}.")

    weeks = regpo["weeks"]
    if not weeks or any(type(week) is not int for week in weeks):
        raise ValueError(f"PFF returned an invalid {WEEK_GROUP} week list.")
    return sorted(set(weeks))


def build_export_tasks(weeks: list[int]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for season in SEASONS:
        for week in weeks:
            for report in REPORTS:
                tasks.append(
                    {
                        "season": season,
                        "week": week,
                        "report": report,
                        "path": EXPORTS_DIR
                        / str(season)
                        / f"week_{week:02d}"
                        / f"{report}.csv",
                        "params": {
                            "season": season,
                            "weekGroup": WEEK_GROUP,
                            "week": week,
                            "format": "csv",
                        },
                    }
                )

        for report in REPORTS:
            tasks.append(
                {
                    "season": season,
                    "week": None,
                    "report": report,
                    "path": EXPORTS_DIR
                    / str(season)
                    / "full_season"
                    / f"{report}.csv",
                    "params": {
                        "season": season,
                        "weekGroup": WEEK_GROUP,
                        "format": "csv",
                    },
                }
            )
    return tasks


def missing_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        task
        for task in tasks
        if not task["path"].is_file() or task["path"].stat().st_size == 0
    ]


def print_plan(weeks: list[int], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending = missing_tasks(tasks)
    print(f"NCAA {WEEK_GROUP} week IDs from PFF metadata: {weeks}")
    print(f"Weekly CSVs: {len(SEASONS)} seasons x {len(weeks)} weeks x {len(REPORTS)} reports")
    print(f"Full-season CSVs: {len(SEASONS)} seasons x {len(REPORTS)} reports")
    print(f"CSV export requests still needed: {len(pending)}")
    print("Week 0 is saved in the week_00 folder.")
    return pending


def save_csv(
    session: requests.Session,
    task: dict[str, Any],
    rate_limiter: ExportRateLimiter,
) -> Path:
    url = f"{API_BASE_URL}/v2/ncaa/positions/reports/{task['report']}"
    response = get_with_retries(
        session,
        url,
        params=task["params"],
        is_export=True,
        rate_limiter=rate_limiter,
    )
    content_type = response.headers.get("Content-Type", "").lower()
    if not content_type.startswith("text/csv"):
        raise ValueError(
            f"Expected a CSV response for {url}, received "
            f"{content_type or 'no Content-Type'}."
        )

    output_path: Path = task["path"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".part")
    temporary_path.write_bytes(response.content)
    temporary_path.replace(output_path)
    return output_path


def create_zip() -> Path:
    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(EXPORTS_DIR.rglob("*")):
            if path.is_file() and not path.name.endswith(".part"):
                archive.write(path, path.relative_to(Path(__file__).parent))
    return ZIP_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan or download PFF NCAA 2024/2025 position-report CSVs."
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="run the planned exports; does not download unless --confirm is also set",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="explicitly confirm the export batch when used with --download",
    )
    args = parser.parse_args()
    if args.confirm and not args.download:
        parser.error("--confirm can only be used with --download.")
    if args.download and not args.confirm:
        parser.error("Bulk downloads require both --download and --confirm.")
    return args


def main() -> int:
    args = parse_args()
    try:
        api_key = get_api_key()
        with requests.Session() as session:
            session.headers.update({"Authorization": f"Bearer {api_key}"})

            if not check_entitlement(session):
                print("No exports started because the account is not entitled.")
                return 0

            weeks = get_regpo_weeks(session)
            tasks = build_export_tasks(weeks)
            pending = print_plan(weeks, tasks)

            if not args.download:
                print("Plan only: no CSV exports were started.")
                print("After approval, run: python pff_export.py --download --confirm")
                return 0

            if not pending:
                print("All requested CSVs already exist; rebuilding the ZIP only.")
            rate_limiter = ExportRateLimiter(SECONDS_BETWEEN_EXPORTS)
            total = len(pending)
            for index, task in enumerate(pending, start=1):
                week_label = (
                    f"week {task['week']}"
                    if task["week"] is not None
                    else "full season"
                )
                print(
                    f"[{index}/{total}] Downloading {task['season']} "
                    f"{week_label} {task['report']}."
                )
                saved_path = save_csv(session, task, rate_limiter)
                print(f"Saved {saved_path.relative_to(Path(__file__).parent)}")

            archive_path = create_zip()
            print(f"Created {archive_path.name}")
    except requests.RequestException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status is not None:
            print(f"PFF API request failed (HTTP {status}).", file=sys.stderr)
        else:
            print(
                f"PFF API request failed ({type(exc).__name__}).",
                file=sys.stderr,
            )
        return 1
    except (RuntimeError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())