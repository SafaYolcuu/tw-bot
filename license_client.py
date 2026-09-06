"""
Tribal Wars Bot — lisans istemcisi (HTTPS activate/validate).
PyQt bağımlılığı yok; ana thread dışında çağırılabilir.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_CONFIG_FILENAME = "license_config.json"


def _resolve_license_api_base() -> str:
    """Oncelik: TWB_LICENSE_API env > license_config.json > localhost."""
    env = (os.environ.get("TWB_LICENSE_API") or "").strip().rstrip("/")
    if env:
        return env
    roots = [Path(__file__).resolve().parent]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)
    for root in roots:
        cfg = root / _CONFIG_FILENAME
        if not cfg.is_file():
            continue
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            base = (data.get("license_api_base") or "").strip().rstrip("/")
            if base and "VDS_IP" not in base.upper() and "BURAYA" not in base.upper():
                return base
        except Exception:
            pass
    return "http://127.0.0.1:8080"


# Prod VDS: license_config.json veya TWB_LICENSE_API
DEFAULT_LICENSE_API_BASE = _resolve_license_api_base()

OFFLINE_GRACE_HOURS_DEFAULT = 72
VALIDATE_INTERVAL_HOURS = 8
HEARTBEAT_INTERVAL_SECONDS = 120  # panelde çevrimiçi için ~2 dk


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def get_machine_id() -> str:
    """Windows makine GUID + node ile kararlı kimlik; hash’lenmiş."""
    parts: list[str] = []
    if platform.system() == "Windows":
        try:
            out = subprocess.check_output(
                [
                    "reg",
                    "query",
                    r"HKLM\SOFTWARE\Microsoft\Cryptography",
                    "/v",
                    "MachineGuid",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in out.splitlines():
                if "MachineGuid" in line:
                    parts.append(line.split()[-1].strip())
                    break
        except Exception:
            pass
    parts.append(platform.node() or "")
    parts.append(platform.system() or "")
    raw = "|".join(parts).encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()[:40]


def get_machine_label() -> str:
    return f"{platform.node()} ({platform.system()} {platform.release()})"[:200]


def _http_json(
    method: str,
    url: str,
    payload: dict | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    data = None
    headers = {
        "User-Agent": "TribalWarsBot-License/1.0",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"ok": True, "status": resp.status, "data": json.loads(body or "{}")}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        detail = err_body
        try:
            parsed = json.loads(err_body or "{}")
            detail = parsed.get("detail") or parsed.get("message") or err_body
        except Exception:
            pass
        return {"ok": False, "status": e.code, "error": str(detail)[:500]}
    except Exception as e:
        return {"ok": False, "status": 0, "error": str(e)[:500]}


class LicenseState:
    """QSettings ile senkronize edilecek alanlar."""

    def __init__(self):
        self.api_base = DEFAULT_LICENSE_API_BASE
        self.license_key = ""
        self.session_token = ""
        self.expires_at: datetime | None = None
        self.last_ok_at: datetime | None = None
        self.active = False
        self.message = ""
        self.grace_hours = OFFLINE_GRACE_HOURS_DEFAULT

    def load_from_settings(self, settings) -> None:
        self.api_base = (
            settings.value("license/api_base", DEFAULT_LICENSE_API_BASE) or DEFAULT_LICENSE_API_BASE
        ).rstrip("/")
        self.license_key = (settings.value("license/key", "") or "").strip().upper()
        self.session_token = (settings.value("license/session_token", "") or "").strip()
        self.expires_at = _parse_iso(settings.value("license/expires_at", "") or "")
        self.last_ok_at = _parse_iso(settings.value("license/last_ok_at", "") or "")
        self.active = bool(settings.value("license/active", False, type=bool))
        self.grace_hours = int(
            settings.value("license/grace_hours", OFFLINE_GRACE_HOURS_DEFAULT) or OFFLINE_GRACE_HOURS_DEFAULT
        )

    def save_to_settings(self, settings) -> None:
        settings.setValue("license/api_base", self.api_base)
        settings.setValue("license/key", self.license_key)
        settings.setValue("license/session_token", self.session_token)
        settings.setValue(
            "license/expires_at",
            self.expires_at.isoformat() if self.expires_at else "",
        )
        settings.setValue(
            "license/last_ok_at",
            self.last_ok_at.isoformat() if self.last_ok_at else "",
        )
        settings.setValue("license/active", bool(self.active))
        settings.setValue("license/grace_hours", int(self.grace_hours))
        settings.sync()

    def apply_server_payload(self, data: dict) -> None:
        self.active = bool(data.get("active"))
        self.message = str(data.get("message") or "")
        if data.get("session_token"):
            self.session_token = str(data["session_token"])
        exp = _parse_iso(data.get("expires_at"))
        if exp:
            self.expires_at = exp
        if data.get("grace_hours") is not None:
            try:
                self.grace_hours = int(data["grace_hours"])
            except Exception:
                pass
        if self.active:
            self.last_ok_at = utcnow()

    def is_entitled(self) -> bool:
        """Çevrimiçi doğrulama veya offline grace ile otomasyon izni."""
        now = utcnow()
        if self.expires_at and self.expires_at <= now:
            return False
        if self.active and self.last_ok_at:
            grace = timedelta(hours=max(1, int(self.grace_hours or OFFLINE_GRACE_HOURS_DEFAULT)))
            if now - self.last_ok_at <= grace:
                return True
        if self.active and self.last_ok_at is None and self.expires_at and self.expires_at > now:
            return True
        return False

    def status_summary(self) -> str:
        if not self.license_key:
            return "Lisans yok"
        if self.is_entitled():
            exp = self.expires_at.strftime("%Y-%m-%d") if self.expires_at else "?"
            return f"Aktif — bitiş {exp}"
        if self.expires_at and self.expires_at <= utcnow():
            return "Süresi dolmuş"
        return self.message or "Doğrulama gerekli"


def activate_license(
    license_key: str,
    *,
    api_base: str | None = None,
    machine_id: str | None = None,
    account_name: str = "",
) -> dict[str, Any]:
    base = (api_base or DEFAULT_LICENSE_API_BASE).rstrip("/")
    mid = machine_id or get_machine_id()
    result = _http_json(
        "POST",
        f"{base}/v1/activate",
        {
            "license_key": license_key.strip().upper(),
            "machine_id": mid,
            "machine_label": get_machine_label(),
            "account_name": (account_name or "").strip(),
        },
    )
    return result


def validate_license(
    license_key: str,
    *,
    session_token: str = "",
    api_base: str | None = None,
    machine_id: str | None = None,
    account_name: str = "",
) -> dict[str, Any]:
    base = (api_base or DEFAULT_LICENSE_API_BASE).rstrip("/")
    mid = machine_id or get_machine_id()
    return _http_json(
        "POST",
        f"{base}/v1/validate",
        {
            "license_key": license_key.strip().upper(),
            "machine_id": mid,
            "session_token": session_token or "",
            "account_name": (account_name or "").strip(),
        },
    )


def send_heartbeat(
    license_key: str,
    *,
    api_base: str | None = None,
    machine_id: str | None = None,
    account_name: str = "",
    bot_running: bool = False,
    botprot_active: bool = False,
    botprot_detail: str = "",
) -> dict[str, Any]:
    base = (api_base or DEFAULT_LICENSE_API_BASE).rstrip("/")
    mid = machine_id or get_machine_id()
    return _http_json(
        "POST",
        f"{base}/v1/heartbeat",
        {
            "license_key": license_key.strip().upper(),
            "machine_id": mid,
            "machine_label": get_machine_label(),
            "account_name": (account_name or "").strip(),
            "bot_running": bool(bot_running),
            "botprot_active": bool(botprot_active),
            "botprot_detail": (botprot_detail or "").strip()[:255],
        },
        timeout=12.0,
    )


def deactivate_this_device(
    license_key: str,
    *,
    api_base: str | None = None,
    machine_id: str | None = None,
) -> dict[str, Any]:
    base = (api_base or DEFAULT_LICENSE_API_BASE).rstrip("/")
    mid = machine_id or get_machine_id()
    return _http_json(
        "POST",
        f"{base}/v1/deactivate-device",
        {"license_key": license_key.strip().upper(), "machine_id": mid},
    )