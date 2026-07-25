"""Session manager for a single bitchat geohash channel: relay selection,
subscriptions, presence heartbeat, dedup, and participant tracking.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from . import crypto, nostr, relays
from .spam import SpamFilter

MENTION_RE = re.compile(r"@(\w+)")

HEARTBEAT_MIN_S = 40
HEARTBEAT_MAX_S = 80
PARTICIPANT_TTL_S = 300  # 5 minutes, per bitchat's "online" window
RELAY_COUNT = 5


@dataclass
class Participant:
    pubkey: str
    nickname: str | None
    last_seen: float


@dataclass
class ChatMessage:
    event_id: str
    pubkey: str
    nickname: str | None
    content: str
    created_at: float
    is_own: bool = False
    mentions_me: bool = False


class ChannelSession:
    def __init__(
        self,
        seed: bytes,
        geohash: str,
        nickname: str,
        on_message: Callable[[ChatMessage], Awaitable[None]],
        on_participants_changed: Callable[[], Awaitable[None]],
        on_status: Callable[[str], Awaitable[None]],
        is_blocked: Callable[[str], bool] = lambda pubkey: False,
        spam_filter: SpamFilter | None = None,
    ):
        self.seed = seed
        self.geohash = geohash
        self.nickname = nickname
        self.on_message = on_message
        self.on_participants_changed = on_participants_changed
        self.on_status = on_status
        self.is_blocked = is_blocked
        self.spam_filter = spam_filter or SpamFilter()

        self.identity = crypto.identity_for_geohash(seed, geohash)
        self.participants: dict[str, Participant] = {}
        self._seen_event_ids: set[str] = set()
        self._connections: list[nostr.RelayConnection] = []
        self._sub_id = nostr.new_sub_id()
        self._heartbeat_task: asyncio.Task | None = None
        self._prune_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        await self.on_status(f"resolving relays for '{self.geohash}' over Tor…")
        try:
            entries = await relays.fetch_geo_relay_directory()
        except Exception:
            entries = []
        urls = relays.closest_relays(self.geohash, entries, count=RELAY_COUNT) if entries else relays.DEFAULT_RELAYS

        connected = 0
        for url in urls:
            conn = nostr.RelayConnection(url)
            conn.on_event = self._handle_event
            conn.on_status = self._handle_relay_status
            try:
                await asyncio.wait_for(conn.connect(), timeout=20)
                self._connections.append(conn)
                connected += 1
            except Exception as exc:
                await self.on_status(f"relay {url} unreachable: {exc}")

        if connected == 0:
            await self.on_status("no relays reachable over Tor — check that Tor is running")
            self._running = False
            return

        await self.on_status(f"connected to {connected}/{len(urls)} relay(s)")

        since = int(time.time()) - 3600
        filt = nostr.geohash_channel_filter(self.geohash, since=since, limit=200)
        for conn in self._connections:
            await conn.subscribe(self._sub_id, [filt])

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._prune_task = asyncio.create_task(self._prune_loop())

    async def stop(self) -> None:
        self._running = False
        for task in (self._heartbeat_task, self._prune_task):
            if task:
                task.cancel()
        for conn in self._connections:
            await conn.close()
        self._connections.clear()

    def resolve_mentions(self, text: str) -> list[str]:
        """@nickname tokens -> pubkeys of current channel participants they match (case-insensitive)."""
        mentioned_names = {m.lower() for m in MENTION_RE.findall(text)}
        if not mentioned_names:
            return []
        return [
            p.pubkey
            for p in self.participants.values()
            if p.nickname and p.nickname.lower() in mentioned_names
        ]

    async def send_message(self, text: str) -> None:
        mention_pubkeys = self.resolve_mentions(text)
        event = nostr.create_geohash_chat_event(
            self.identity, self.geohash, text, nickname=self.nickname, mention_pubkeys=mention_pubkeys
        )
        self._seen_event_ids.add(event["id"])
        for conn in self._connections:
            if conn.connected:
                await conn.publish(event)
        await self.on_message(
            ChatMessage(
                event_id=event["id"],
                pubkey=self.identity.pubkey_hex,
                nickname=self.nickname,
                content=text,
                created_at=event["created_at"],
                is_own=True,
            )
        )

    async def _handle_relay_status(self, status: str) -> None:
        await self.on_status(f"relay {status}")

    async def _handle_event(self, sub_id: str, event: dict) -> None:
        if event["id"] in self._seen_event_ids:
            return
        self._seen_event_ids.add(event["id"])

        pubkey = event["pubkey"]
        if self.is_blocked(pubkey):
            return

        tags = {t[0]: t[1:] for t in event["tags"] if t}
        # Presence events (kind 20001) never carry an "n" tag by protocol design, so a
        # missing nickname here must NOT erase one already learned from an earlier chat
        # message -- keep the last known nickname unless this event supplies a new one.
        new_nickname = tags.get("n", [None])[0]
        now = time.time()

        existing = self.participants.get(pubkey)
        nickname = new_nickname or (existing.nickname if existing else None)
        changed = existing is None or existing.nickname != nickname
        self.participants[pubkey] = Participant(pubkey=pubkey, nickname=nickname, last_seen=now)
        if changed:
            await self.on_participants_changed()

        if event["kind"] == nostr.KIND_EPHEMERAL_CHAT and event.get("content"):
            is_own = pubkey == self.identity.pubkey_hex
            if not is_own and not self.spam_filter.allow(pubkey, event["content"]):
                return
            mentions_me = self.identity.pubkey_hex in tags.get("p", [])
            await self.on_message(
                ChatMessage(
                    event_id=event["id"],
                    pubkey=pubkey,
                    nickname=nickname,
                    content=event["content"],
                    created_at=event["created_at"],
                    is_own=is_own,
                    mentions_me=mentions_me,
                )
            )

    async def _heartbeat_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(random.uniform(HEARTBEAT_MIN_S, HEARTBEAT_MAX_S))
                event = nostr.create_geohash_presence_event(self.identity, self.geohash)
                for conn in self._connections:
                    if conn.connected:
                        await conn.publish(event)
        except asyncio.CancelledError:
            pass

    async def _prune_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(30)
                cutoff = time.time() - PARTICIPANT_TTL_S
                stale = [pk for pk, p in self.participants.items() if p.last_seen < cutoff]
                if stale:
                    for pk in stale:
                        del self.participants[pk]
                    await self.on_participants_changed()
        except asyncio.CancelledError:
            pass

    def online_participants(self) -> list[Participant]:
        cutoff = time.time() - PARTICIPANT_TTL_S
        return sorted(
            (p for p in self.participants.values() if p.last_seen >= cutoff),
            key=lambda p: p.last_seen,
            reverse=True,
        )
