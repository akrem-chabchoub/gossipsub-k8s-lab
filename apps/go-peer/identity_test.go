package main

import "testing"

func TestIdentityDeterministic(t *testing.T) {
	_, a1, err := identityFor("go-peer-0")
	if err != nil {
		t.Fatal(err)
	}
	_, a2, _ := identityFor("go-peer-0")
	_, b, _ := identityFor("go-peer-1")
	if a1 != a2 {
		t.Fatal("same name must give same peer ID")
	}
	if a1 == b {
		t.Fatal("different names must give different peer IDs")
	}
}
