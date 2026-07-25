"""Local blocklist: pubkeys whose messages (channel or DM) are dropped before display.

Stored as pubkey_hex -> label (whatever the user typed to /block them), not a bare
set: once blocked, a channel participant stops appearing in the live participant
list at all (that's the block working) -- so their nickname would otherwise be
unrecoverable and /unblock <nickname> could never find them again.

Note: geohash-channel identities are per-channel and unlinkable by design
(see crypto.py), so blocking someone in one geohash does not block the same
person in another geohash -- only their DM identity (crypto.DeviceIdentity)
is stable enough to block globally.
"""

from __future__ import annotations

import json

from .crypto import CONFIG_DIR

BLOCKLIST_FILE = CONFIG_DIR / "blocked.json"


def load() -> dict[str, str]:
    """pubkey_hex -> label (last known nickname/alias at block time)"""
    if BLOCKLIST_FILE.exists():
        try:
            return json.loads(BLOCKLIST_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save(blocked: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    BLOCKLIST_FILE.write_text(json.dumps(blocked, indent=2))
