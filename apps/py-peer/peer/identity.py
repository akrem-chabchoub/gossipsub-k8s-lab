"""Deterministic identity from a stable peer name, identical to apps/go-peer/identity.go.

seed = sha256(name); Ed25519 key from seed; peer ID from the public key.
Lab-only design: every peer can compute every other peer's ID from names alone.
"""
import hashlib

from libp2p.crypto.ed25519 import create_new_key_pair
from libp2p.peer.id import ID


def seed_for(name: str) -> bytes:
    return hashlib.sha256(name.encode()).digest()


def identity_for(name: str):
    """Return (key_pair, peer_id) for a peer name."""
    key_pair = create_new_key_pair(seed_for(name))
    return key_pair, ID.from_pubkey(key_pair.public_key)
