"""NIP-44 v2 encryption: secp256k1 ECDH, HKDF, custom padding, ChaCha20, HMAC-SHA256.

Implemented directly from the official spec (nostr-protocol/nips/44.md) and
verified against its published test vectors. Real, standard NIP-44 — not
bitchat's own slightly-different HKDF+XChaCha20Poly1305 variant — so this
also happens to interop with other real NIP-44/NIP-17 Nostr clients, not
just other bitchat-tui instances.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os

from coincurve import PublicKey

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

VERSION = 2
MIN_PLAINTEXT_SIZE = 1
MAX_PLAINTEXT_SIZE = 0xFFFFFFFF
EXTENDED_PREFIX_THRESHOLD = 65536


class Nip44Error(Exception):
    pass


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    hash_len = 32
    n = math.ceil(length / hash_len)
    t = b""
    okm = b""
    for i in range(1, n + 1):
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
    return okm[:length]


def ecdh_shared_x(privkey_bytes: bytes, pubkey_xonly_hex: str) -> bytes:
    """secp256k1 scalar multiplication privkey * pubkey, unhashed 32-byte x coordinate.

    Nostr pubkeys are x-only (BIP-340); BIP-340 keygen always chooses the
    private key such that the resulting point has even Y, so prefixing with
    0x02 reconstructs the correct point.
    """
    compressed = b"\x02" + bytes.fromhex(pubkey_xonly_hex)
    point = PublicKey(compressed).multiply(privkey_bytes)
    return point.format(compressed=True)[1:]  # drop parity prefix -> x coordinate


def get_conversation_key(privkey_bytes: bytes, pubkey_xonly_hex: str) -> bytes:
    shared_x = ecdh_shared_x(privkey_bytes, pubkey_xonly_hex)
    return _hkdf_extract(salt=b"nip44-v2", ikm=shared_x)


def get_message_keys(conversation_key: bytes, nonce: bytes) -> tuple[bytes, bytes, bytes]:
    if len(conversation_key) != 32:
        raise Nip44Error("invalid conversation_key length")
    if len(nonce) != 32:
        raise Nip44Error("invalid nonce length")
    keys = _hkdf_expand(conversation_key, info=nonce, length=76)
    return keys[0:32], keys[32:44], keys[44:76]


def _calc_padded_len(unpadded_len: int) -> int:
    if unpadded_len <= 32:
        return 32
    next_power = 1 << (math.floor(math.log2(unpadded_len - 1)) + 1)
    chunk = 32 if next_power <= 256 else next_power // 8
    return chunk * (((unpadded_len - 1) // chunk) + 1)


def _pad(plaintext: str) -> bytes:
    unpadded = plaintext.encode("utf-8")
    n = len(unpadded)
    if n < MIN_PLAINTEXT_SIZE or n > MAX_PLAINTEXT_SIZE:
        raise Nip44Error("invalid plaintext length")
    if n >= EXTENDED_PREFIX_THRESHOLD:
        prefix = b"\x00\x00" + n.to_bytes(4, "big")
    else:
        prefix = n.to_bytes(2, "big")
    suffix = bytes(_calc_padded_len(n) - n)
    return prefix + unpadded + suffix


def _unpad(padded: bytes) -> str:
    first_two = int.from_bytes(padded[0:2], "big")
    if first_two == 0:
        unpadded_len = int.from_bytes(padded[2:6], "big")
        if unpadded_len < EXTENDED_PREFIX_THRESHOLD:
            raise Nip44Error("invalid padding")
        prefix_len = 6
    else:
        unpadded_len = first_two
        prefix_len = 2
    unpadded = padded[prefix_len : prefix_len + unpadded_len]
    if (
        unpadded_len == 0
        or len(unpadded) != unpadded_len
        or len(padded) != prefix_len + _calc_padded_len(unpadded_len)
    ):
        raise Nip44Error("invalid padding")
    return unpadded.decode("utf-8")


def _chacha20(key: bytes, nonce12: bytes, data: bytes) -> bytes:
    full_nonce = b"\x00\x00\x00\x00" + nonce12  # counter=0 (LE) || 12-byte nonce, per RFC 8439
    cipher = Cipher(algorithms.ChaCha20(key, full_nonce), mode=None)
    encryptor = cipher.encryptor()
    return encryptor.update(data) + encryptor.finalize()


def _hmac_aad(key: bytes, message: bytes, aad: bytes) -> bytes:
    if len(aad) != 32:
        raise Nip44Error("AAD must be 32 bytes")
    return hmac.new(key, aad + message, hashlib.sha256).digest()


def encrypt(plaintext: str, conversation_key: bytes, nonce: bytes | None = None) -> str:
    nonce = nonce if nonce is not None else os.urandom(32)
    chacha_key, chacha_nonce, hmac_key = get_message_keys(conversation_key, nonce)
    padded = _pad(plaintext)
    ciphertext = _chacha20(chacha_key, chacha_nonce, padded)
    mac = _hmac_aad(hmac_key, ciphertext, aad=nonce)
    return base64.b64encode(bytes([VERSION]) + nonce + ciphertext + mac).decode("ascii")


def decrypt(payload: str, conversation_key: bytes) -> str:
    if not payload or payload[0] == "#":
        raise Nip44Error("unknown version")
    if len(payload) < 132:
        raise Nip44Error("invalid payload size")
    data = base64.b64decode(payload)
    if len(data) < 99:
        raise Nip44Error("invalid data size")
    version = data[0]
    if version != VERSION:
        raise Nip44Error(f"unknown version {version}")
    nonce = data[1:33]
    ciphertext = data[33 : len(data) - 32]
    mac = data[len(data) - 32 :]
    chacha_key, chacha_nonce, hmac_key = get_message_keys(conversation_key, nonce)
    calculated_mac = _hmac_aad(hmac_key, ciphertext, aad=nonce)
    if not hmac.compare_digest(calculated_mac, mac):
        raise Nip44Error("invalid MAC")
    padded_plaintext = _chacha20(chacha_key, chacha_nonce, ciphertext)
    return _unpad(padded_plaintext)
