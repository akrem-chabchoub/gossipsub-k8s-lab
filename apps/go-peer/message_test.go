package main

import (
	"strings"
	"testing"
)

func TestMessageRoundTrip(t *testing.T) {
	in := Message{RunID: "r1", PublisherID: "12D3Koo...", Seq: 7, PublishedNs: 123, Impl: "go", Payload: MakePayload(16)}
	b, err := in.Encode()
	if err != nil {
		t.Fatal(err)
	}
	out, err := DecodeMessage(b)
	if err != nil {
		t.Fatal(err)
	}
	if out != in {
		t.Fatalf("round trip mismatch: got %+v want %+v", out, in)
	}
}

func TestDecodeRejectsMissingFields(t *testing.T) {
	cases := []string{
		`{"runId":"","publisherId":"p","seq":1,"publishedAtNs":1}`,
		`{"runId":"r","publisherId":"","seq":1,"publishedAtNs":1}`,
		`{"runId":"r","publisherId":"p","seq":0,"publishedAtNs":1}`,
		`{"runId":"r","publisherId":"p","seq":1,"publishedAtNs":0}`,
		`not json`,
	}
	for _, c := range cases {
		if _, err := DecodeMessage([]byte(c)); err == nil {
			t.Errorf("expected error for %s", c)
		}
	}
}

func TestMakePayloadSize(t *testing.T) {
	if got := len(MakePayload(1024)); got != 1024 {
		t.Fatalf("payload len %d want 1024", got)
	}
	if MakePayload(0) != "" || !strings.HasPrefix(MakePayload(3), "xxx") {
		t.Fatal("payload edge cases wrong")
	}
}

func TestDeduper(t *testing.T) {
	d := NewDeduper()
	if d.Seen("r1", "p", 1) {
		t.Fatal("first receipt must not be a duplicate")
	}
	if !d.Seen("r1", "p", 1) {
		t.Fatal("second receipt must be a duplicate")
	}
	if d.Seen("r1", "q", 1) {
		t.Fatal("same seq from another publisher is distinct")
	}
	if d.Seen("r2", "p", 1) {
		t.Fatal("same seq in a new run (publisher restart) is distinct")
	}
}

func TestLatencySeconds(t *testing.T) {
	if got, ok := LatencySeconds(1_000_000_000, 1_250_000_000); !ok || got != 0.25 {
		t.Fatalf("got %v %v want 0.25 true", got, ok)
	}
	if _, ok := LatencySeconds(2_000_000_000, 1_000_000_000); ok {
		t.Fatal("negative latency must be flagged as clock skew")
	}
}
