"""Study 4 execution settings. Production submission defaults to disabled."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from models.study4_live import ExecutionEnvironment

from .. import config


def execution_db_path() -> Path:
    override = os.environ.get("SFP_STUDY4_LEDGER")
    if override:
        return Path(override).expanduser().resolve()
    return (config.data_dir() / "execution" / "study4.sqlite").resolve()


def artifacts_dir() -> Path:
    override = os.environ.get("SFP_STUDY4_ARTIFACTS")
    if override:
        return Path(override).expanduser().resolve()
    return (config.data_dir() / "execution" / "study4").resolve()


@dataclass(frozen=True)
class ExecutionSettings:
    mode: str
    production_enabled: bool
    kill_switch: bool
    account_alias: str
    account_fingerprint: str
    confirmation_secret: str
    target_bucket: float | None
    production_cap: float | None

    @property
    def configured(self) -> bool:
        return self.mode in {"sandbox", "production"}

    @property
    def environment(self) -> ExecutionEnvironment | None:
        if self.mode == "sandbox":
            return ExecutionEnvironment.SANDBOX
        if self.mode == "production":
            return ExecutionEnvironment.PRODUCTION
        return None

    @property
    def submissions_allowed(self) -> bool:
        if (not self.configured or self.kill_switch
                or not self.account_fingerprint or not self.confirmation_secret):
            return False
        if self.mode == "production" and not self.production_enabled:
            return False
        return True

    def setup_requirements(
        self,
        *,
        capital_ready: bool,
        environ: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Presence-only checklist. Never includes secret or account values."""
        values = os.environ if environ is None else environ

        def present(name: str) -> bool:
            return bool(values.get(name, "").strip())

        items = [
            {
                "id": "execution_mode",
                "label": "Execution mode",
                "ready": self.configured,
                "action": (
                    "Set SFP_STUDY4_EXECUTION_MODE=sandbox in app.env "
                    "(use production only when unlocking live money), then restart the server."
                ),
            },
            {
                "id": "account_fingerprint",
                "label": "Dedicated account fingerprint",
                "ready": bool(self.account_fingerprint),
                "action": (
                    "Set SFP_STUDY4_ACCOUNT_FINGERPRINT to the SHA-256 of "
                    "sandbox:<account> or production:<account>. Do not store the raw account number."
                ),
            },
        ]
        if self.mode == "production":
            items.append({
                "id": "execution_credentials",
                "label": "Dedicated production Tastytrade credentials",
                "ready": present("SFP_STUDY4_PRODUCTION_TT_CLIENT_SECRET")
                and present("SFP_STUDY4_PRODUCTION_TT_REFRESH_TOKEN"),
                "action": (
                    "Set SFP_STUDY4_PRODUCTION_TT_CLIENT_SECRET and "
                    "SFP_STUDY4_PRODUCTION_TT_REFRESH_TOKEN. Ordinary TT_* credentials cannot submit these orders."
                ),
            })
            items.append({
                "id": "production_unlock",
                "label": "Production submission unlock",
                "ready": self.production_enabled,
                "action": "Set SFP_STUDY4_PRODUCTION_ENABLED=true only after sandbox rehearsal.",
            })
            items.append({
                "id": "production_cap",
                "label": "Production pilot cap",
                "ready": self.production_cap is not None,
                "action": (
                    "Set SFP_STUDY4_PRODUCTION_CAP to the owner-entered ceiling. "
                    "It is never increased automatically."
                ),
            })
        else:
            items.append({
                "id": "execution_credentials",
                "label": "Dedicated sandbox Tastytrade credentials",
                "ready": present("SFP_STUDY4_SANDBOX_TT_CLIENT_SECRET")
                and present("SFP_STUDY4_SANDBOX_TT_REFRESH_TOKEN"),
                "action": (
                    "Set SFP_STUDY4_SANDBOX_TT_CLIENT_SECRET and "
                    "SFP_STUDY4_SANDBOX_TT_REFRESH_TOKEN. Ordinary TT_* credentials cannot submit these orders."
                ),
            })
        items.append({
            "id": "confirmation_secret",
            "label": "Confirmation HMAC secret",
            "ready": bool(self.confirmation_secret),
            "action": (
                "Set SFP_STUDY4_CONFIRMATION_SECRET. It binds the one-time confirmation to the frozen plan."
            ),
        })
        items.append({
            "id": "target_capital",
            "label": "Target capital version",
            "ready": capital_ready,
            "action": (
                "Set SFP_STUDY4_TARGET_BUCKET, then initialize capital on this page. "
                "Capital changes are dated versions; they do not rewrite history."
            ),
        })
        return items


def load_settings(environ: dict[str, str] | None = None) -> ExecutionSettings:
    values = os.environ if environ is None else environ
    mode = values.get("SFP_STUDY4_EXECUTION_MODE", "").strip().lower()
    if mode in {"", "disabled", "off"}:
        mode = ""
    if mode not in {"", "sandbox", "production"}:
        raise ValueError("SFP_STUDY4_EXECUTION_MODE must be sandbox, production, or empty")
    production_enabled = values.get("SFP_STUDY4_PRODUCTION_ENABLED", "").strip().lower() in {
        "1", "true", "yes",
    }
    kill = values.get("SFP_STUDY4_KILL_SWITCH", "").strip().lower() in {"1", "true", "yes"}
    target = values.get("SFP_STUDY4_TARGET_BUCKET", "").strip()
    cap = values.get("SFP_STUDY4_PRODUCTION_CAP", "").strip()
    secret = values.get("SFP_STUDY4_CONFIRMATION_SECRET", "").strip()
    return ExecutionSettings(
        mode=mode,
        production_enabled=production_enabled,
        kill_switch=kill,
        account_alias=values.get("SFP_STUDY4_ACCOUNT_ALIAS", "").strip() or "study4",
        account_fingerprint=values.get("SFP_STUDY4_ACCOUNT_FINGERPRINT", "").strip(),
        confirmation_secret=secret,
        target_bucket=float(target) if target else None,
        production_cap=float(cap) if cap else None,
    )
