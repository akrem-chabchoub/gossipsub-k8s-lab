"""Environment-driven config. Same variables as apps/go-peer/main.go."""
import os
import socket
import time
from dataclasses import dataclass, field


@dataclass
class Config:
    name: str
    topic: str
    run_id: str
    publisher: bool
    peer_names: list
    dns_suffix: str
    listen_port: int
    metrics_port: int
    publish_rate: float
    payload_bytes: int
    publish_for_s: float
    redial_interval_s: float


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key, "")
    return int(v) if v else default


def _env_float(key: str, default: float) -> float:
    v = os.environ.get(key, "")
    return float(v) if v else default


def load_config(env=None) -> Config:
    env = os.environ if env is None else env
    name = env.get("PEER_NAME") or socket.gethostname()
    publish_rate = float(env.get("PUBLISH_RATE") or 10)
    payload_bytes = int(env.get("PAYLOAD_BYTES") or 1024)
    if publish_rate <= 0:
        raise ValueError("PUBLISH_RATE must be > 0")
    if not (0 <= payload_bytes <= 65536):
        raise ValueError("PAYLOAD_BYTES must be 0..65536")
    run_id = env.get("RUN_ID") or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return Config(
        name=name,
        topic=env.get("TOPIC") or "lab/v1",
        run_id=run_id,
        publisher=name == (env.get("PUBLISHER_NAME") or ""),
        peer_names=[p.strip() for p in (env.get("PEER_NAMES") or name).split(",") if p.strip()],
        dns_suffix=env.get("PEER_DNS_SUFFIX") or "",
        listen_port=int(env.get("LISTEN_PORT") or 4001),
        metrics_port=int(env.get("METRICS_PORT") or 9100),
        publish_rate=publish_rate,
        payload_bytes=payload_bytes,
        publish_for_s=float(env.get("PUBLISH_FOR_SECONDS") or 0),
        redial_interval_s=float(env.get("REDIAL_INTERVAL_SECONDS") or 5),
    )
