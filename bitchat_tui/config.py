"""Small local config store: nickname + last-used geohash channel."""

from __future__ import annotations

import json

from .crypto import CONFIG_DIR

CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {"nickname": "", "last_geohash": "9q5"}


def load() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            return {**DEFAULTS, **data}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULTS)


def save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    current = load()
    current.update(data)
    CONFIG_FILE.write_text(json.dumps(current, indent=2))
