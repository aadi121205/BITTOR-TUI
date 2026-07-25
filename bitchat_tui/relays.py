"""Relay selection: bitchat's default relay list plus its geo-indexed relay directory.

bitchat picks relays for a geohash channel from a community-maintained CSV of
(relay_url, lat, lon), choosing the closest N by haversine distance with a
deterministic tie-break by hostname — so every client lands on the same relay
set for a given channel. We replicate that exactly for real interop.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from aiohttp_socks import ProxyConnector

from .crypto import CONFIG_DIR
from .geohash import decode_center
from .nostr import DEFAULT_TOR_SOCKS

logger = logging.getLogger(__name__)

DEFAULT_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.primal.net",
    "wss://offchain.pub",
]

GEO_RELAY_CSV_URL = (
    "https://raw.githubusercontent.com/permissionlesstech/georelays/refs/heads/main/nostr_relays.csv"
)
CACHE_FILE = CONFIG_DIR / "georelays_cache.csv"
CACHE_MAX_AGE_SECONDS = 24 * 3600


@dataclass(frozen=True)
class RelayEntry:
    host: str
    lat: float
    lon: float


def _parse_csv(text: str) -> list[RelayEntry]:
    entries: list[RelayEntry] = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row or len(row) < 3:
            continue
        if "relay url" in row[0].lower():  # header row
            continue
        host, lat_s, lon_s = row[0].strip(), row[1].strip(), row[2].strip()
        if not host:
            continue
        host = host.replace("wss://", "").replace("ws://", "").strip("/")
        try:
            entries.append(RelayEntry(host=host, lat=float(lat_s), lon=float(lon_s)))
        except ValueError:
            continue
    return entries


async def fetch_geo_relay_directory(socks_proxy: str = DEFAULT_TOR_SOCKS) -> list[RelayEntry]:
    """Fetch (over Tor) and cache bitchat's community geo-relay directory."""
    if CACHE_FILE.exists():
        age = time.time() - CACHE_FILE.stat().st_mtime
        if age < CACHE_MAX_AGE_SECONDS:
            entries = _parse_csv(CACHE_FILE.read_text())
            if entries:
                return entries
    try:
        connector = ProxyConnector.from_url(socks_proxy, rdns=True)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(GEO_RELAY_CSV_URL, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                text = await resp.text()
        entries = _parse_csv(text)
        if entries:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(text)
            return entries
    except Exception as exc:
        logger.warning("geo-relay directory fetch failed: %r", exc)
    if CACHE_FILE.exists():
        return _parse_csv(CACHE_FILE.read_text())
    return []


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def closest_relays(geohash: str, entries: list[RelayEntry], count: int = 5) -> list[str]:
    if not entries:
        return list(DEFAULT_RELAYS)
    lat, lon = decode_center(geohash)
    ranked = sorted(
        entries,
        key=lambda e: (_haversine_km(lat, lon, e.lat, e.lon), e.host),
    )
    return [f"wss://{e.host}" for e in ranked[:count]]
