"""Local address book: alias -> DM identity pubkey, for the /dm and /addcontact commands."""

from __future__ import annotations

import json

from .crypto import CONFIG_DIR

CONTACTS_FILE = CONFIG_DIR / "contacts.json"


def load() -> dict[str, str]:
    """alias (lowercase) -> pubkey_hex"""
    if CONTACTS_FILE.exists():
        try:
            return json.loads(CONTACTS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save(contacts: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONTACTS_FILE.write_text(json.dumps(contacts, indent=2))


def add(alias: str, pubkey_hex: str) -> dict[str, str]:
    contacts = load()
    contacts[alias.lower()] = pubkey_hex
    save(contacts)
    return contacts


def alias_for(pubkey_hex: str) -> str | None:
    for alias, pk in load().items():
        if pk == pubkey_hex:
            return alias
    return None
