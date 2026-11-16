"""py-peer: one py-libp2p GossipSub participant in the observability lab.

Same env contract, metric names, and JSON log fields as apps/go-peer.
"""
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import trio
from libp2p import new_host
from libp2p.peer.id import ID
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.pubsub.gossipsub import PROTOCOL_ID_V11, GossipSub
from libp2p.pubsub.pubsub import Pubsub
from libp2p.tools.anyio_service import background_trio_service
from multiaddr import Multiaddr
from prometheus_client import generate_latest

from peer.config import load_config
from peer.identity import identity_for
from peer.message import Deduper, Message, decode_message, latency_seconds, make_payload
from peer.metrics import Metrics

READY = threading.Event()


def log(event, level="INFO", **fields):
    print(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z", "level": level,
                      "impl": "py", "peer": CFG.name, "event": event, **fields}), flush=True)


def serve_http(port, registry):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/metrics":
                body = generate_latest(registry)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/healthz":
                self.send_response(200 if READY.is_set() else 503)
                self.end_headers()
            elif self.path == "/livez":
                self.send_response(200)
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):  # keep stdout for JSON logs only
            pass

    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


async def receive_loop(sub, self_id, dedupe, m, names):
    while True:
        msg = await sub.get()
        from_name = names.get(ID(msg.from_id).to_base58(), ID(msg.from_id).to_base58())
        try:
            pm = decode_message(msg.data)
        except Exception as err:  # noqa: BLE001 - any decode failure is counted and logged
            m.decode_errors.inc()
            log("decode_error", "WARN", err=str(err))
            continue
        if pm.publisherId == self_id:
            continue
        if dedupe.seen(pm.publisherId, pm.seq):
            m.duplicates.inc()
            continue
        recv_ns = time.time_ns()
        latency, ok = latency_seconds(pm.publishedAtNs, recv_ns)
        if not ok:
            m.clock_skew.inc()
        m.delivery_latency.observe(latency)
        m.deliveries(names.get(pm.publisherId, "")).inc()
        m.last_recv_ts.set_to_current_time()
        log("receive", runId=pm.runId, publisherId=pm.publisherId,
            publisherName=names.get(pm.publisherId, ""), seq=pm.seq,
            publishedAtNs=pm.publishedAtNs, receivedAtNs=recv_ns,
            latencyMs=latency * 1000, fromName=from_name)


async def publish_loop(pubsub, topic, self_id, router, cfg, m):
    while not router.mesh.get(topic):
        await trio.sleep(0.2)
    log("publish_start", runId=cfg.run_id, rate=cfg.publish_rate, payloadBytes=cfg.payload_bytes)
    payload = make_payload(cfg.payload_bytes)
    interval = 1.0 / cfg.publish_rate
    start = trio.current_time()
    seq = 0
    while True:
        seq += 1
        # Schedule against the start time so jitter does not accumulate.
        await trio.sleep_until(start + seq * interval)
        if cfg.publish_for_s > 0 and trio.current_time() - start >= cfg.publish_for_s:
            log("publish_done", runId=cfg.run_id, lastSeq=seq - 1)
            return
        m.publish_attempted.inc()
        msg = Message(runId=cfg.run_id, publisherId=self_id, seq=seq,
                      publishedAtNs=time.time_ns(), impl="py", payload=payload)
        try:
            await pubsub.publish(cfg.topic, msg.encode())
        except Exception as err:  # noqa: BLE001
            m.publish_errors.inc()
            log("publish_error", "WARN", seq=seq, err=str(err))
            continue
        m.publish_success.inc()
        m.last_publish_ts.set_to_current_time()
        log("publish", runId=cfg.run_id, seq=seq, publishedAtNs=msg.publishedAtNs)


async def resolve_target(addr):
    """Resolve a /dns4/<name>/tcp/<port>/p2p/<id> target to (peer ID, /ip4 multiaddr with no /p2p).

    Root cause of the Python recovery stall: py-libp2p keeps every address it has ever learned
    for a peer. After a pod restart the peerstore still held the peer's previous IP, which now
    belonged to another lab pod. Dialing that address reached a live libp2p host with a different
    identity and failed with "handshake failed when upgrading to secure connection"
    (reproduced in-cluster; see REPORT.md). Resolving the DNS name on every attempt and clearing
    stored addresses first means only the current pod IP is dialed.
    """
    ma = Multiaddr(addr)
    host_name = ma.value_for_protocol("dns4")
    port = ma.value_for_protocol("tcp")
    pid = ma.value_for_protocol("p2p")
    infos = await trio.to_thread.run_sync(
        socket.getaddrinfo, host_name, int(port), socket.AF_INET, socket.SOCK_STREAM)
    ip = infos[0][4][0]
    return ID.from_base58(pid), Multiaddr(f"/ip4/{ip}/tcp/{port}"), pid


async def redial_loop(host, targets, cfg, m):
    while True:
        connected = {str(p) for p in host.get_connected_peers()}
        for pid, addr in targets.items():
            if pid in connected:
                continue
            try:
                peer_id, ip_addr, pid_str = await resolve_target(addr)
                ps = host.get_peerstore()
                ps.clear_addrs(peer_id)
                ps.add_addr(peer_id, ip_addr, 120)
                # py-libp2p's 60 s negative cache would block retries; clear it first.
                net = host.get_network()
                if hasattr(net, "unblock_peer"):
                    net.unblock_peer(peer_id)
                # 15 s: a restarted peer can take longer than 4 s to dial. See tradeoffs.md D12.
                with trio.fail_after(15):
                    await host.connect(info_from_p2p_addr(Multiaddr(f"{ip_addr}/p2p/{pid_str}")))
            except Exception as err:  # noqa: BLE001
                m.dial_errors.inc()
                log("dial_error", "WARN", peerId=pid, err=str(err) or type(err).__name__)
        await trio.sleep(cfg.redial_interval_s)


async def sample_loop(host, router, cfg, m, names):
    """Once per second: connected and mesh gauges, plus connect/disconnect and mesh-change events."""
    prev_conn, prev_mesh = set(), set()
    while True:
        conn = {str(p) for p in host.get_connected_peers()}
        mesh = {str(p) for p in router.mesh.get(cfg.topic, set())}
        m.connected_peers.set(len(conn))
        m.mesh_peers(cfg.topic).set(len(mesh))
        for pid in conn - prev_conn:
            log("connect", peerName=names.get(pid, pid))
        for pid in prev_conn - conn:
            log("disconnect", peerName=names.get(pid, pid))
        for pid in mesh - prev_mesh:
            log("graft", topic=cfg.topic, peerName=names.get(pid, pid))
        for pid in prev_mesh - mesh:
            log("prune", topic=cfg.topic, peerName=names.get(pid, pid))
        prev_conn, prev_mesh = conn, mesh
        await trio.sleep(1)


async def main():
    self_key, self_id_obj = identity_for(CFG.name)
    self_id = str(self_id_obj)
    names, targets = {}, {}
    for n in CFG.peer_names:
        _, pid_obj = identity_for(n)
        pid = str(pid_obj)
        names[pid] = n
        # Only the lexicographically smaller name dials each pair. Dialing from both sides at
        # once caused handshake failures and connection churn in the py-only validation run.
        if CFG.name < n:
            targets[pid] = f"/dns4/{n}{CFG.dns_suffix}/tcp/{CFG.listen_port}/p2p/{pid}"

    listen = Multiaddr(f"/ip4/0.0.0.0/tcp/{CFG.listen_port}")
    host = new_host(key_pair=self_key, listen_addrs=[listen])
    # py-libp2p defaults (120 s heartbeat, 10 msg/s per topic) drop messages at this load, so
    # the lab sets a 1 s heartbeat and a rate ceiling above mesh fan-in x publish rate.
    router = GossipSub(protocols=[PROTOCOL_ID_V11], degree=6, degree_low=4, degree_high=12,
                       heartbeat_interval=1, max_messages_per_topic_per_second=500.0)
    pubsub = Pubsub(host, router)
    m = Metrics("py", CFG.name)
    threading.Thread(target=serve_http, args=(CFG.metrics_port, m.registry), daemon=True).start()

    async with host.run(listen_addrs=[listen]), background_trio_service(pubsub), background_trio_service(router):
        sub = await pubsub.subscribe(CFG.topic)
        READY.set()
        log("started", publisher=CFG.publisher, runId=CFG.run_id, peerId=self_id,
            listenPort=CFG.listen_port, targets=len(targets))
        dedupe = Deduper()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(receive_loop, sub, self_id, dedupe, m, names)
            nursery.start_soon(redial_loop, host, targets, CFG, m)
            nursery.start_soon(sample_loop, host, router, CFG, m, names)
            if CFG.publisher:
                nursery.start_soon(publish_loop, pubsub, CFG.topic, self_id, router, CFG, m)


CFG = None

if __name__ == "__main__":
    try:
        CFG = load_config()
    except ValueError as err:
        print(f"config: {err}", file=sys.stderr)
        sys.exit(2)
    trio.run(main)
