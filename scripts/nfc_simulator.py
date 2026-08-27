#!/usr/bin/env python3
"""Interactive NFC hardware emulator for Nexa Care V2 live API testing.

The simulator exercises the provider-gated emergency NFC break-glass snapshot
flow. Direct routine consent issuance is retired; routine access must use the
discovery-bound patient approval flow in the application.

No secrets are persisted. The provider API key is read from CLINIC_API_KEY or
prompted with hidden input at startup.
"""

from __future__ import annotations

import json
import os
import sys
from getpass import getpass
from typing import Any

import requests

DEFAULT_BASE_URL = "https://nexa-care.onrender.com"
BASE_URL_ENV_NAMES = ("NEXA_CARE_API_BASE_URL", "NEXA_CARE_BASE_URL")
REQUEST_TIMEOUT_SECONDS = 30

GREEN = "[92m"
RED = "[91m"
CYAN = "[96m"
YELLOW = "[93m"
BOLD = "[1m"
RESET = "[0m"


class NexaSimulator:
    """Small synchronous CLI client for provider-authenticated hardware tests."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            }
        )

    def run(self) -> None:
        """Run the interactive menu loop until the operator exits."""

        print(f"{CYAN}Connected target:{RESET} {self.base_url}")
        while True:
            self._print_menu()
            choice = input(f"{CYAN}Select option [1-2]: {RESET}").strip()
            if choice == "1":
                self.simulate_emergency_tap()
            elif choice == "2":
                print(f"{GREEN}Exiting simulator.{RESET}")
                return
            else:
                print_error("Invalid option. Choose 1 or 2.")

    def simulate_emergency_tap(self) -> None:
        """POST a scanned NFC UID to the emergency read-card endpoint."""

        card_uid = input(
            f"{CYAN}Enter scanned NFC UID (e.g., 04:A2:B4...): {RESET}"
        ).strip()
        if not card_uid:
            print_error("NFC UID is required.")
            return

        response = self._request(
            "POST",
            "/api/v2/emergency/read-card",
            json={"card_uid": card_uid},
        )
        if response is None:
            return

        print_success("Emergency Snapshot")
        print_json(response)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Send one API request and return JSON, printing safe errors on failure."""

        url = f"{self.base_url}{path}"
        try:
            response = self.session.request(
                method,
                url,
                json=json,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.Timeout:
            print_error(
                f"Request timed out after {REQUEST_TIMEOUT_SECONDS}s: {method} {path}"
            )
            return None
        except requests.RequestException as exc:
            print_error(f"Network error while calling {method} {path}: {exc}")
            return None

        if response.status_code >= 400:
            print_http_error(response)
            return None

        try:
            payload = response.json()
        except ValueError:
            print_error("API returned a non-JSON response.")
            print(response.text)
            return None

        if not isinstance(payload, dict):
            print_error("API returned JSON, but not an object payload.")
            print_json(payload)
            return None

        return payload

    @staticmethod
    def _print_menu() -> None:
        print()
        print(f"{BOLD}{CYAN}Nexa Care NFC Simulator{RESET}")
        print(f"{CYAN}1.{RESET} Simulate Emergency NFC Tap (Break-Glass)")
        print(f"{CYAN}2.{RESET} Exit")


def configured_base_url() -> str:
    """Return the configured API base URL, falling back to Render."""

    for env_name in BASE_URL_ENV_NAMES:
        value = os.getenv(env_name)
        if value:
            return value.strip()
    return DEFAULT_BASE_URL


def read_api_key() -> str:
    """Read the provider API key from environment or hidden prompt."""

    api_key = os.getenv("CLINIC_API_KEY")
    if api_key:
        return api_key.strip()

    api_key = getpass(f"{CYAN}Enter CLINIC_API_KEY: {RESET}").strip()
    if not api_key:
        print_error("CLINIC_API_KEY is required.")
        raise SystemExit(1)
    return api_key


def print_json(payload: Any) -> None:
    """Pretty-print JSON-compatible data."""

    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def print_success(message: str) -> None:
    """Print a green success heading."""

    print(f"{GREEN}{BOLD}{message}{RESET}")


def print_error(message: str) -> None:
    """Print a red error message."""

    print(f"{RED}ERROR: {message}{RESET}")


def print_http_error(response: requests.Response) -> None:
    """Display API failures without exposing tracebacks."""

    detail: Any
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = body.get("detail") or body.get("message") or body
        else:
            detail = body
    except ValueError:
        detail = response.text.strip() or response.reason

    status_label = {
        403: "Forbidden",
        404: "Not Found",
        500: "Server Error",
    }.get(response.status_code, "HTTP Error")

    print_error(f"{status_label} ({response.status_code})")
    if isinstance(detail, (dict, list)):
        print_json(detail)
    else:
        print(f"{RED}{detail}{RESET}")


def main() -> int:
    """CLI entrypoint."""

    simulator = NexaSimulator(base_url=configured_base_url(), api_key=read_api_key())
    try:
        simulator.run()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted. Exiting simulator.{RESET}")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
