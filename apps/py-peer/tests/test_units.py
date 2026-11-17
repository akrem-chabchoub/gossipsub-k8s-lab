import unittest

from peer.config import load_config
from peer.identity import identity_for
from peer.message import Deduper, ForwarderLog, Message, decode_message, latency_seconds, make_payload


class IdentityTests(unittest.TestCase):
    def test_deterministic_and_matches_go_peer_zero(self):
        _, a = identity_for("go-peer-0")
        _, b = identity_for("go-peer-0")
        _, c = identity_for("go-peer-1")
        self.assertEqual(str(a), str(b))
        self.assertNotEqual(str(a), str(c))
        # Same ID printed by apps/go-peer identityFor("go-peer-0") (cross-implementation parity).
        self.assertEqual(str(a), "12D3KooWJLiAJmGBfQFQSDsyY9DuKAMKG8bsczbCnCYXF8hZozRK")


class MessageTests(unittest.TestCase):
    def test_round_trip(self):
        m = Message(runId="r1", publisherId="p", seq=7, publishedAtNs=123, impl="py", payload=make_payload(16))
        self.assertEqual(decode_message(m.encode()), m)

    def test_rejects_missing_fields(self):
        for raw in [
            b'{"runId":"","publisherId":"p","seq":1,"publishedAtNs":1}',
            b'{"runId":"r","publisherId":"","seq":1,"publishedAtNs":1}',
            b'{"runId":"r","publisherId":"p","seq":0,"publishedAtNs":1}',
            b'{"runId":"r","publisherId":"p","seq":1,"publishedAtNs":0}',
            b"not json",
        ]:
            with self.assertRaises(Exception):
                decode_message(raw)

    def test_payload_size(self):
        self.assertEqual(len(make_payload(1024)), 1024)
        self.assertEqual(make_payload(0), "")

    def test_latency(self):
        self.assertEqual(latency_seconds(1_000_000_000, 1_250_000_000), (0.25, True))
        self.assertEqual(latency_seconds(2_000_000_000, 1_000_000_000), (0.0, False))

    def test_deduper(self):
        d = Deduper()
        self.assertFalse(d.seen("r1", "p", 1))
        self.assertTrue(d.seen("r1", "p", 1))
        self.assertFalse(d.seen("r1", "q", 1))
        self.assertFalse(d.seen("r2", "p", 1))  # publisher restarted with a new run

    def test_forwarder_log(self):
        f = ForwarderLog()
        f.record("fwd", b"author", b"\x01")
        self.assertEqual(f.pop(b"author", b"\x01"), "fwd")
        self.assertEqual(f.pop(b"author", b"\x01"), "")  # popped once


class ConfigTests(unittest.TestCase):
    def test_publisher_and_defaults(self):
        c = load_config({"PEER_NAME": "a", "PEER_NAMES": "a,b", "PUBLISHER_NAME": "a"})
        self.assertTrue(c.publisher)
        self.assertEqual(c.peer_names, ["a", "b"])
        self.assertEqual(c.payload_bytes, 1024)

    def test_validation(self):
        with self.assertRaises(ValueError):
            load_config({"PEER_NAME": "a", "PUBLISH_RATE": "0"})


if __name__ == "__main__":
    unittest.main()
