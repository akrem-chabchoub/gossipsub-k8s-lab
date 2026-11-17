"""Wire format shared with apps/go-peer/message.go . Field names must match."""
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    runId: str
    publisherId: str
    seq: int
    publishedAtNs: int
    impl: str
    payload: str

    def encode(self) -> bytes:
        return json.dumps(self.__dict__).encode()


def decode_message(raw: bytes) -> Message:
    d = json.loads(raw)
    if not d.get("runId") or not d.get("publisherId") or not d.get("seq", 0) > 0 or not d.get("publishedAtNs", 0) > 0:
        raise ValueError(f"message missing required fields: {d.get('runId')!r} {d.get('publisherId')!r} "
                         f"seq={d.get('seq')} ts={d.get('publishedAtNs')}")
    return Message(runId=d["runId"], publisherId=d["publisherId"], seq=int(d["seq"]),
                   publishedAtNs=int(d["publishedAtNs"]), impl=d.get("impl", ""), payload=d.get("payload", ""))


def make_payload(n: int) -> str:
    return "x" * n if n > 0 else ""


def latency_seconds(published_ns: int, received_ns: int):
    """Return (seconds, ok). ok=False means receive precedes publish (clock skew)."""
    d = received_ns - published_ns
    return (0.0, False) if d < 0 else (d / 1e9, True)


class Deduper:
    def __init__(self):
        self._seen = set()

    def seen(self, run_id: str, publisher_id: str, seq: int) -> bool:
        """True if (run, publisher, seq) was already recorded; records it otherwise.

        The run ID is part of the key because seq restarts at 1 when a publisher restarts with a new run.
        """
        key = (run_id, publisher_id, seq)
        if key in self._seen:
            return True
        self._seen.add(key)
        return False


class ForwarderLog:
    """Remembers which peer forwarded each message, keyed by (author, seqno).

    py-libp2p's subscription queue yields only the message, whose from_id is the original author.
    Topic validators are the one place the library passes the forwarding peer, and they run once
    per new message before it is queued for subscribers. Go reports the same peer as ReceivedFrom.
    """

    MAX_ENTRIES = 10_000  # bound memory if the queue ever drops a validated message

    def __init__(self):
        self._by_mid = {}

    def record(self, forwarder: str, from_id: bytes, seqno: bytes) -> None:
        if len(self._by_mid) >= self.MAX_ENTRIES:
            self._by_mid.clear()
        self._by_mid[(from_id, seqno)] = forwarder

    def pop(self, from_id: bytes, seqno: bytes) -> str:
        return self._by_mid.pop((from_id, seqno), "")
