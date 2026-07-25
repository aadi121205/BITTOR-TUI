"""Identity + signing for the bitchat Nostr geohash-channel transport.

Replicates bitchat's NostrIdentityBridge per-geohash key derivation:
each geohash gets its own, unlinkable secp256k1 keypair derived from a
single locally-stored device seed via HMAC-SHA256(seed, geohash || iter).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path

from coincurve import PrivateKey, PublicKeyXOnly

from . import bech32

CONFIG_DIR = Path(os.environ.get("BITCHAT_TUI_HOME", Path.home() / ".config" / "bitchat-tui"))
SEED_FILE = CONFIG_DIR / "device_seed.json"
DEVICE_IDENTITY_FILE = CONFIG_DIR / "device_identity.json"

# secp256k1 group order
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


@dataclass(frozen=True)
class GeohashIdentity:
    geohash: str
    private_key: bytes  # 32 bytes
    public_key_xonly: bytes  # 32 bytes
    pubkey_hex: str
    npub: str


@dataclass(frozen=True)
class DeviceIdentity:
    """A persistent, geohash-independent keypair used for private DMs (NIP-17)."""

    private_key: bytes  # 32 bytes
    public_key_xonly: bytes  # 32 bytes
    pubkey_hex: str
    npub: str


def load_or_create_device_seed() -> bytes:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if SEED_FILE.exists():
        data = json.loads(SEED_FILE.read_text())
        return bytes.fromhex(data["seed_hex"])
    seed = os.urandom(32)
    SEED_FILE.write_text(json.dumps({"seed_hex": seed.hex()}))
    try:
        SEED_FILE.chmod(0o600)
    except OSError:
        pass
    return seed


def is_valid_scalar(b: bytes) -> bool:
    n = int.from_bytes(b, "big")
    return 0 < n < _N


def derive_geohash_private_key(seed: bytes, geohash: str) -> bytes:
    """HMAC-SHA256(seed, geohash_utf8 || uint32_be(iteration)), first valid scalar wins."""
    msg = geohash.encode("utf-8")
    for iteration in range(10):
        candidate = hmac.new(
            seed, msg + struct.pack(">I", iteration), hashlib.sha256
        ).digest()
        if is_valid_scalar(candidate):
            return candidate
    # fallback per bitchat source
    fallback = hashlib.sha256(seed + msg).digest()
    if is_valid_scalar(fallback):
        return fallback
    raise RuntimeError("failed to derive a valid secp256k1 scalar for geohash identity")


def identity_for_geohash(seed: bytes, geohash: str) -> GeohashIdentity:
    privkey_bytes = derive_geohash_private_key(seed, geohash)
    pk = PrivateKey(privkey_bytes)
    xonly = pk.public_key.format(compressed=True)[1:]  # drop 0x02/0x03 prefix -> 32-byte X
    pubkey_hex = xonly.hex()
    npub = bech32.encode("npub", xonly)
    return GeohashIdentity(
        geohash=geohash,
        private_key=privkey_bytes,
        public_key_xonly=xonly,
        pubkey_hex=pubkey_hex,
        npub=npub,
    )


def identity_from_private_key(private_key: bytes) -> tuple[bytes, str, str]:
    pk = PrivateKey(private_key)
    xonly = pk.public_key.format(compressed=True)[1:]
    pubkey_hex = xonly.hex()
    npub = bech32.encode("npub", xonly)
    return xonly, pubkey_hex, npub


def load_or_create_device_identity() -> DeviceIdentity:
    """A single persistent keypair for this device, used for private DMs.

    Unlike geohash identities (derived fresh per-channel, unlinkable to each
    other), this one is stable across all channels so contacts can DM you.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if DEVICE_IDENTITY_FILE.exists():
        data = json.loads(DEVICE_IDENTITY_FILE.read_text())
        private_key = bytes.fromhex(data["private_key_hex"])
    else:
        private_key = os.urandom(32)
        while not is_valid_scalar(private_key):
            private_key = os.urandom(32)
        DEVICE_IDENTITY_FILE.write_text(json.dumps({"private_key_hex": private_key.hex()}))
        try:
            DEVICE_IDENTITY_FILE.chmod(0o600)
        except OSError:
            pass
    xonly, pubkey_hex, npub = identity_from_private_key(private_key)
    return DeviceIdentity(private_key=private_key, public_key_xonly=xonly, pubkey_hex=pubkey_hex, npub=npub)


def npub_to_pubkey_hex(npub: str) -> str:
    hrp, data = bech32.decode(npub)
    if hrp != "npub" or len(data) != 32:
        raise ValueError("not a valid npub")
    return data.hex()


def pubkey_hex_to_npub(pubkey_hex: str) -> str:
    return bech32.encode("npub", bytes.fromhex(pubkey_hex))


def resolve_pubkey_hex(value: str) -> str:
    """Accept either an npub or a raw 64-char hex pubkey."""
    value = value.strip()
    if value.startswith("npub1"):
        return npub_to_pubkey_hex(value)
    if len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value):
        return value.lower()
    raise ValueError("expected an npub (npub1...) or a 64-character hex pubkey")


def sign_schnorr(private_key: bytes, message32: bytes) -> str:
    return PrivateKey(private_key).sign_schnorr(message32).hex()


def verify_schnorr(pubkey_xonly_hex: str, message32: bytes, sig_hex: str) -> bool:
    try:
        pub = PublicKeyXOnly(bytes.fromhex(pubkey_xonly_hex))
        return pub.verify(bytes.fromhex(sig_hex), message32)
    except Exception:
        return False
