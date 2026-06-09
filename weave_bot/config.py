"""Configuration, loaded from environment variables with sensible defaults."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SPREADSHEET_ID = "1hc-ELFp0W1F8W5IuUeOosNJpcWwydjC0_aJQ4UYwswI"
DEFAULT_ANALYTICS_URL = "https://app.getweave.com/analytics/phone/main"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    spreadsheet_id: str = DEFAULT_SPREADSHEET_ID
    daily_summary_tab: str = "Daily Summary"
    by_line_tab: str = "By Line"
    timezone: str = "America/Los_Angeles"
    analytics_url: str = DEFAULT_ANALYTICS_URL

    weave_email: str = ""
    weave_password: str = ""

    headless: bool = True
    # When true, a By Line vs. Answered mismatch blocks the write entirely.
    # When false (default), rows are written and the run exits non-zero so
    # GitHub still notifies you.
    strict_validation: bool = False

    storage_state_file: Path = field(default_factory=lambda: Path(".auth/storage_state.json"))
    debug_dir: Path = field(default_factory=lambda: Path("debug"))

    @classmethod
    def from_env(cls) -> "Config":
        cfg = cls(
            spreadsheet_id=os.environ.get("SPREADSHEET_ID", DEFAULT_SPREADSHEET_ID),
            daily_summary_tab=os.environ.get("DAILY_SUMMARY_TAB", "Daily Summary"),
            by_line_tab=os.environ.get("BY_LINE_TAB", "By Line"),
            timezone=os.environ.get("REPORT_TIMEZONE", "America/Los_Angeles"),
            analytics_url=os.environ.get("WEAVE_ANALYTICS_URL", DEFAULT_ANALYTICS_URL),
            weave_email=os.environ.get("WEAVE_EMAIL", ""),
            weave_password=os.environ.get("WEAVE_PASSWORD", ""),
            headless=_env_bool("HEADLESS", True),
            strict_validation=_env_bool("STRICT_VALIDATION", False),
            storage_state_file=Path(os.environ.get("WEAVE_STORAGE_STATE_FILE", ".auth/storage_state.json")),
            debug_dir=Path(os.environ.get("DEBUG_DIR", "debug")),
        )
        cfg.materialize_storage_state()
        return cfg

    def materialize_storage_state(self) -> None:
        """If WEAVE_STORAGE_STATE_B64 is set and no state file exists yet,
        decode it to disk so Playwright can reuse the saved session
        (useful when login requires MFA and was captured locally)."""
        b64 = os.environ.get("WEAVE_STORAGE_STATE_B64", "").strip()
        if b64 and not self.storage_state_file.exists():
            self.storage_state_file.parent.mkdir(parents=True, exist_ok=True)
            self.storage_state_file.write_bytes(base64.b64decode(b64))
