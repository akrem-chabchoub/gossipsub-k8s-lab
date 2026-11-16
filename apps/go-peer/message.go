package main

import (
	"encoding/json"
	"fmt"
	"strings"
	"sync"
)

// Message is the application payload published on the GossipSub topic.
type Message struct {
	RunID       string `json:"runId"`
	PublisherID string `json:"publisherId"`
	Seq         int    `json:"seq"`
	PublishedNs int64  `json:"publishedAtNs"`
	Impl        string `json:"impl"`
	Payload     string `json:"payload"`
}

// Encode serializes a message to JSON.
func (m Message) Encode() ([]byte, error) {
	return json.Marshal(m)
}

// DecodeMessage parses a message and rejects ones missing required fields.
func DecodeMessage(b []byte) (Message, error) {
	var m Message
	if err := json.Unmarshal(b, &m); err != nil {
		return Message{}, fmt.Errorf("decode message: %w", err)
	}
	if m.RunID == "" || m.PublisherID == "" || m.Seq <= 0 || m.PublishedNs <= 0 {
		return Message{}, fmt.Errorf("message missing required fields: run=%q publisher=%q seq=%d ts=%d",
			m.RunID, m.PublisherID, m.Seq, m.PublishedNs)
	}
	return m, nil
}

// MakePayload returns a filler string of exactly n bytes (ASCII).
func MakePayload(n int) string {
	if n <= 0 {
		return ""
	}
	return strings.Repeat("x", n)
}

// Deduper tracks (publisher, seq) pairs already seen. Safe for concurrent use.
type Deduper struct {
	mu   sync.Mutex
	seen map[string]struct{}
}

func NewDeduper() *Deduper {
	return &Deduper{seen: make(map[string]struct{})}
}

// Seen reports whether the message was already recorded, and records it if not.
// It returns true for duplicates.
func (d *Deduper) Seen(publisherID string, seq int) bool {
	key := fmt.Sprintf("%s/%d", publisherID, seq)
	d.mu.Lock()
	defer d.mu.Unlock()
	if _, ok := d.seen[key]; ok {
		return true
	}
	d.seen[key] = struct{}{}
	return false
}

// LatencySeconds converts publish and receive timestamps (ns) to seconds.
// Negative values mean the clocks disagree; they are clamped to zero and
// the caller should count them as skew events.
func LatencySeconds(publishedNs, receivedNs int64) (float64, bool) {
	d := receivedNs - publishedNs
	if d < 0 {
		return 0, false
	}
	return float64(d) / 1e9, true
}
