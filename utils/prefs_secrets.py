"""Protect saved API keys with Windows DPAPI and omit them from portable backups."""
from __future__ import annotations

import base64
import logging


_SECTIONS = ("ollama_chat", "gameassist", "game_assist", "gamebrain")
_PROTECTED = "api_key_dpapi"
_LOG = logging.getLogger("deskassist.utils.prefs_secrets")


def protect_api_keys(prefs):
    """Replace legacy plaintext in the payload before it is written to disk."""
    for name in _SECTIONS:
        section = prefs.get(name)
        if not isinstance(section, dict):
            continue
        key = section.pop("api_key", "")
        if key:
            import win32crypt
            encrypted = win32crypt.CryptProtectData(
                key.encode("utf-8"), "Desktop Assistant API key", None, None, None, 1
            )
            section[_PROTECTED] = base64.b64encode(encrypted).decode("ascii")


def unlock_api_keys(prefs):
    """Load keys for the current Windows user, preserving unreadable protected values."""
    for name in _SECTIONS:
        section = prefs.get(name)
        if not isinstance(section, dict) or section.get("api_key") or not section.get(_PROTECTED):
            continue
        try:
            import win32crypt
            _, raw = win32crypt.CryptUnprotectData(
                base64.b64decode(section[_PROTECTED], validate=True), None, None, None, 1
            )
            section["api_key"] = raw.decode("utf-8")
            section.pop(_PROTECTED)
        except Exception:
            _LOG.warning("无法解密本机 API 密钥 section=%s", name)


def redact_api_keys(prefs):
    """Remove key material from a copy of the preference payload."""
    for name in _SECTIONS:
        section = prefs.get(name)
        if isinstance(section, dict):
            section.pop("api_key", None)
            section.pop(_PROTECTED, None)


def preserve_matching_keys(prefs, local):
    """Keep local keys when an archive omits them and the configured endpoint agrees."""
    for name in _SECTIONS:
        section = prefs.get(name)
        current = local.get(name)
        if not isinstance(section, dict):
            continue
        # A protected blob from another backup may belong to a different Windows user.
        section.pop(_PROTECTED, None)
        if not isinstance(current, dict):
            continue
        endpoint = "api_base" if name == "ollama_chat" else "base_url"
        restored = str(section.get(endpoint) or "").strip().rstrip("/")
        previous = str(current.get(endpoint) or "").strip().rstrip("/")
        if restored == previous and not section.get("api_key"):
            if current.get("api_key"):
                section["api_key"] = current["api_key"]
            elif current.get(_PROTECTED):
                section[_PROTECTED] = current[_PROTECTED]
