"""NIP-17 private DMs + NIP-59 gift wrap, built on the verified nip44 module.

Real end-to-end encryption: relays and any intermediate party see only an
opaque kind-1059 event signed by a random, single-use key. Only the holder
of the recipient's private key can unwrap it.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass

from coincurve import PrivateKey

from . import crypto, nip44
from .nostr import build_and_sign_event, compute_event_id, verify_event

logger = logging.getLogger(__name__)

KIND_DM_RUMOR = 14
KIND_SEAL = 13
KIND_GIFT_WRAP = 1059

TWO_DAYS = 2 * 24 * 3600


def _randomized_past_timestamp() -> int:
    return int(time.time() - random.uniform(0, TWO_DAYS))


def _canonical(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class EphemeralIdentity:
    private_key: bytes
    public_key_xonly: bytes
    pubkey_hex: str


def _new_ephemeral_identity() -> EphemeralIdentity:
    private_key = os.urandom(32)
    while not crypto.is_valid_scalar(private_key):
        private_key = os.urandom(32)
    xonly, pubkey_hex, _ = crypto.identity_from_private_key(private_key)
    return EphemeralIdentity(private_key=private_key, public_key_xonly=xonly, pubkey_hex=pubkey_hex)


def _create_rumor(sender: crypto.DeviceIdentity, recipient_pubkey_hex: str, content: str) -> dict:
    created_at = int(time.time())
    tags = [["p", recipient_pubkey_hex]]
    event_id = compute_event_id(sender.pubkey_hex, created_at, KIND_DM_RUMOR, tags, content)
    return {
        "id": event_id,
        "pubkey": sender.pubkey_hex,
        "created_at": created_at,
        "kind": KIND_DM_RUMOR,
        "tags": tags,
        "content": content,
    }


def _create_seal(rumor: dict, sender: crypto.DeviceIdentity, recipient_pubkey_hex: str) -> dict:
    conv_key = nip44.get_conversation_key(sender.private_key, recipient_pubkey_hex)
    encrypted_rumor = nip44.encrypt(_canonical(rumor), conv_key)
    return build_and_sign_event(
        sender, KIND_SEAL, [], encrypted_rumor, created_at=_randomized_past_timestamp()
    )


def _create_gift_wrap(seal: dict, recipient_pubkey_hex: str) -> dict:
    ephemeral = _new_ephemeral_identity()
    conv_key = nip44.get_conversation_key(ephemeral.private_key, recipient_pubkey_hex)
    encrypted_seal = nip44.encrypt(_canonical(seal), conv_key)
    return build_and_sign_event(
        ephemeral,
        KIND_GIFT_WRAP,
        [["p", recipient_pubkey_hex]],
        encrypted_seal,
        created_at=_randomized_past_timestamp(),
    )


def create_dm_gift_wrap(sender: crypto.DeviceIdentity, recipient_pubkey_hex: str, content: str) -> dict:
    """Build the full rumor -> seal -> gift-wrap chain for a private DM. Returns the gift-wrap event to publish."""
    rumor = _create_rumor(sender, recipient_pubkey_hex, content)
    seal = _create_seal(rumor, sender, recipient_pubkey_hex)
    return _create_gift_wrap(seal, recipient_pubkey_hex)


@dataclass(frozen=True)
class UnwrappedDM:
    sender_pubkey_hex: str
    content: str
    created_at: int


def unwrap_gift_wrap(gift_wrap_event: dict, recipient: crypto.DeviceIdentity) -> UnwrappedDM | None:
    """Decrypt+verify an inbound kind-1059 event. Returns None (and logs) on any failure."""
    try:
        outer_conv_key = nip44.get_conversation_key(recipient.private_key, gift_wrap_event["pubkey"])
        seal = json.loads(nip44.decrypt(gift_wrap_event["content"], outer_conv_key))

        if not verify_event(seal):
            logger.warning("DM seal has invalid signature, dropping")
            return None

        inner_conv_key = nip44.get_conversation_key(recipient.private_key, seal["pubkey"])
        rumor = json.loads(nip44.decrypt(seal["content"], inner_conv_key))

        if seal["pubkey"] != rumor.get("pubkey"):
            logger.warning("DM rumor pubkey does not match seal signer, possible spoofing attempt, dropping")
            return None

        return UnwrappedDM(
            sender_pubkey_hex=seal["pubkey"],
            content=rumor.get("content", ""),
            created_at=int(rumor.get("created_at", time.time())),
        )
    except Exception as exc:
        logger.debug("failed to unwrap gift wrap %s: %r", gift_wrap_event.get("id", "?"), exc)
        return None
