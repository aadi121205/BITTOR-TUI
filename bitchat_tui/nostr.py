"""Nostr event construction/signing and a minimal relay client routed over Tor.

Implements just what bitchat's public geohash channels need: NIP-01 event
framing (REQ/EVENT/EOSE/CLOSE/OK/NOTICE) and bitchat's kind 20000 (ephemeral
chat) / kind 20001 (presence) geohash events. No NIP-44 encryption is needed
here since geohash channel content is public/plaintext.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

import aiohttp
from aiohttp_socks import ProxyConnector

from . import crypto

logger = logging.getLogger(__name__)

KIND_EPHEMERAL_CHAT = 20000
KIND_PRESENCE = 20001

DEFAULT_TOR_SOCKS = "socks5://127.0.0.1:9050"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def compute_event_id(pubkey_hex: str, created_at: int, kind: int, tags: list, content: str) -> str:
    serialized = _canonical_json([0, pubkey_hex, created_at, kind, tags, content])
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class SigningIdentity(Protocol):
    pubkey_hex: str
    private_key: bytes


def build_and_sign_event(
    identity: SigningIdentity,
    kind: int,
    tags: list[list[str]],
    content: str,
    created_at: int | None = None,
) -> dict:
    created_at = created_at if created_at is not None else int(time.time())
    event_id = compute_event_id(identity.pubkey_hex, created_at, kind, tags, content)
    sig = crypto.sign_schnorr(identity.private_key, bytes.fromhex(event_id))
    return {
        "id": event_id,
        "pubkey": identity.pubkey_hex,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": sig,
    }


def verify_event(event: dict) -> bool:
    try:
        recomputed = compute_event_id(
            event["pubkey"], event["created_at"], event["kind"], event["tags"], event["content"]
        )
        if recomputed != event["id"]:
            return False
        return crypto.verify_schnorr(event["pubkey"], bytes.fromhex(recomputed), event["sig"])
    except Exception:
        return False


def create_geohash_chat_event(
    identity: crypto.GeohashIdentity,
    geohash: str,
    content: str,
    nickname: str | None = None,
    mention_pubkeys: list[str] | None = None,
) -> dict:
    tags = [["g", geohash]]
    if nickname:
        nickname = nickname.strip()
        if nickname:
            tags.append(["n", nickname])
    for pk in mention_pubkeys or []:
        tags.append(["p", pk])
    return build_and_sign_event(identity, KIND_EPHEMERAL_CHAT, tags, content)


def create_geohash_presence_event(identity: crypto.GeohashIdentity, geohash: str) -> dict:
    return build_and_sign_event(identity, KIND_PRESENCE, [["g", geohash]], "")


@dataclass
class NostrFilter:
    kinds: list[int] | None = None
    authors: list[str] | None = None
    tags: dict[str, list[str]] = field(default_factory=dict)  # e.g. {"g": ["9q8yy"]}
    since: int | None = None
    limit: int | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {}
        if self.kinds:
            d["kinds"] = self.kinds
        if self.authors:
            d["authors"] = self.authors
        for tag, values in self.tags.items():
            d[f"#{tag}"] = values
        if self.since is not None:
            d["since"] = self.since
        if self.limit is not None:
            d["limit"] = self.limit
        return d


def geohash_channel_filter(geohash: str, since: int | None = None, limit: int = 200) -> NostrFilter:
    return NostrFilter(
        kinds=[KIND_EPHEMERAL_CHAT, KIND_PRESENCE], tags={"g": [geohash]}, since=since, limit=limit
    )


class RelayConnection:
    """A single relay websocket connection, tunneled through Tor's SOCKS proxy."""

    def __init__(self, url: str, socks_proxy: str = DEFAULT_TOR_SOCKS):
        self.url = url
        self.socks_proxy = socks_proxy
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._listen_task: asyncio.Task | None = None
        self.on_event: Callable[[str, dict], Awaitable[None]] | None = None
        self.on_eose: Callable[[str], Awaitable[None]] | None = None
        self.on_ok: Callable[[str, bool, str], Awaitable[None]] | None = None
        self.on_status: Callable[[str], Awaitable[None]] | None = None
        self.connected = False

    async def connect(self, timeout: float = 20.0) -> None:
        connector = ProxyConnector.from_url(self.socks_proxy, rdns=True)
        self._session = aiohttp.ClientSession(connector=connector)
        self._ws = await self._session.ws_connect(self.url, timeout=timeout, heartbeat=30)
        self.connected = True
        self._listen_task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                await self._dispatch(payload)
        except Exception as exc:  # connection dropped
            logger.debug("relay %s listen loop ended: %r", self.url, exc)
        finally:
            self.connected = False
            if self.on_status:
                await self.on_status("disconnected")

    async def _dispatch(self, payload: list) -> None:
        if not payload:
            return
        msg_type = payload[0]
        if msg_type == "EVENT" and len(payload) >= 3:
            sub_id, event = payload[1], payload[2]
            if verify_event(event) and self.on_event:
                await self.on_event(sub_id, event)
        elif msg_type == "EOSE" and len(payload) >= 2:
            if self.on_eose:
                await self.on_eose(payload[1])
        elif msg_type == "OK" and len(payload) >= 3:
            if self.on_ok:
                await self.on_ok(payload[1], bool(payload[2]), payload[3] if len(payload) > 3 else "")
        elif msg_type == "NOTICE":
            logger.debug("NOTICE from %s: %s", self.url, payload[1] if len(payload) > 1 else "")

    async def subscribe(self, sub_id: str, filters: list[NostrFilter]) -> None:
        assert self._ws is not None
        req = ["REQ", sub_id] + [f.to_dict() for f in filters]
        await self._ws.send_str(_canonical_json(req))

    async def close_subscription(self, sub_id: str) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.send_str(_canonical_json(["CLOSE", sub_id]))

    async def publish(self, event: dict) -> None:
        assert self._ws is not None
        await self._ws.send_str(_canonical_json(["EVENT", event]))

    async def close(self) -> None:
        if self._listen_task:
            self._listen_task.cancel()
        if self._ws is not None:
            await self._ws.close()
        if self._session is not None:
            await self._session.close()
        self.connected = False


def new_sub_id() -> str:
    return uuid.uuid4().hex[:16]


async def check_tor_socks(socks_proxy: str = DEFAULT_TOR_SOCKS, timeout: float = 3.0) -> bool:
    """Quick reachability check for the local Tor SOCKS port before doing real work."""
    from urllib.parse import urlparse

    parsed = urlparse(socks_proxy)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 9050
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False
