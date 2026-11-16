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

    def seen(self, publisher_id: str, seq: int) -> bool:
        """True if (publisher, seq) was already recorded; records it otherwise."""
        key = (publisher_id, seq)
        if key in self._seen:
            return True
        self._seen.add(key)
        return False
