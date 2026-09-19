"""Web 任务持久化前统一清理敏感字段。"""

from __future__ import annotations

from typing import Any

_SENSITIVE_MARKERS = ("key", "secret", "token", "password", "credential", "authorization")
_REDACTED = "[已脱敏]"


def redact_value(value: Any, *, secrets: tuple[str, ...] = ()) -> Any:
    """递归清理敏感键，并替换文本中已知的密钥值。"""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in _SENSITIVE_MARKERS):
                cleaned[str(key)] = _REDACTED
            else:
                cleaned[str(key)] = redact_value(item, secrets=secrets)
        return cleaned
    if isinstance(value, (list, tuple, set)):
        return [redact_value(item, secrets=secrets) for item in value]
    if isinstance(value, str):
        cleaned = value
        for secret in secrets:
            if len(secret) >= 4:
                cleaned = cleaned.replace(secret, _REDACTED)
        return cleaned
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)
