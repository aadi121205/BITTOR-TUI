"""Session manager for private end-to-end encrypted DMs (NIP-17/NIP-59) over Tor.

Runs independently of whichever geohash channel is active, since a device's
DM identity is persistent rather than per-channel. Both ends must be
bitchat-tui (or another real NIP-17 client) with each other's npub added.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from . import crypto, nip17, nostr, relays
from .spam import SpamFilter

GIFT_WRAP_LOOKBACK_S = 2 * 24 * 3600  # matches NIP-59's created_at randomization window


class DMSession:
    def __init__(
        self,
        identity: crypto.DeviceIdentity,
        on_dm: Callable[[nip17.UnwrappedDM], Awaitable[None]],
        on_status: Callable[[str], Awaitable[None]],
        is_blocked: Callable[[str], bool] = lambda pubkey: False,
        spam_filter: SpamFilter | None = None,
    ):
        self.identity = identity
        self.on_dm = on_dm
        self.on_status = on_status
        self.is_blocked = is_blocked
        self.spam_filter = spam_filter or SpamFilter()
        self._connections: list[nostr.RelayConnection] = []
        self._seen_event_ids: set[str] = set()
        self._sub_id = nostr.new_sub_id()
        self._running = False

    async def start(self) -> None:
        self._running = True
        connected = 0
        for url in relays.DEFAULT_RELAYS:
            conn = nostr.RelayConnection(url)
            conn.on_event = self._handle_event
            try:
                await asyncio.wait_for(conn.connect(), timeout=20)
                self._connections.append(conn)
                connected += 1
            except Exception as exc:
                await self.on_status(f"DM relay {url} unreachable: {exc}")

        if connected == 0:
            await self.on_status("DMs unavailable: no relays reachable over Tor")
            self._running = False
            return

        since = int(time.time()) - GIFT_WRAP_LOOKBACK_S
        filt = nostr.NostrFilter(kinds=[nip17.KIND_GIFT_WRAP], tags={"p": [self.identity.pubkey_hex]}, since=since)
        for conn in self._connections:
            await conn.subscribe(self._sub_id, [filt])

        await self.on_status(f"DMs ready ({connected}/{len(relays.DEFAULT_RELAYS)} relays) — your npub: {self.identity.npub}")

    async def stop(self) -> None:
        self._running = False
        for conn in self._connections:
            await conn.close()
        self._connections.clear()

    async def send_dm(self, recipient_pubkey_hex: str, content: str) -> None:
        if not self._connections:
            raise RuntimeError("no DM relay connections available")
        gift_wrap = nip17.create_dm_gift_wrap(self.identity, recipient_pubkey_hex, content)
        for conn in self._connections:
            if conn.connected:
                await conn.publish(gift_wrap)

    async def _handle_event(self, sub_id: str, event: dict) -> None:
        if event["id"] in self._seen_event_ids:
            return
        self._seen_event_ids.add(event["id"])
        unwrapped = nip17.unwrap_gift_wrap(event, self.identity)
        if (
            unwrapped is not None
            and not self.is_blocked(unwrapped.sender_pubkey_hex)
            and self.spam_filter.allow(unwrapped.sender_pubkey_hex, unwrapped.content)
        ):
            await self.on_dm(unwrapped)
