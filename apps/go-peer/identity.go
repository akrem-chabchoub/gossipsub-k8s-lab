package main

import (
	"crypto/ed25519"
	"crypto/sha256"
	"fmt"

	"github.com/libp2p/go-libp2p/core/crypto"
	"github.com/libp2p/go-libp2p/core/peer"
)

// seedFor derives a 32-byte Ed25519 seed from a stable peer name.
// Lab-only design: every peer can compute every other peer's ID from names
// alone, so discovery works over DNS without a registry. Not for production.
func seedFor(name string) []byte {
	sum := sha256.Sum256([]byte(name))
	return sum[:]
}

// identityFor returns the libp2p private key and peer ID for a peer name.
func identityFor(name string) (crypto.PrivKey, peer.ID, error) {
	priv, err := crypto.UnmarshalEd25519PrivateKey(ed25519.NewKeyFromSeed(seedFor(name)))
	if err != nil {
		return nil, "", fmt.Errorf("derive key for %q: %w", name, err)
	}
	id, err := peer.IDFromPrivateKey(priv)
	if err != nil {
		return nil, "", fmt.Errorf("peer id for %q: %w", name, err)
	}
	return priv, id, nil
}
