"""Same metric names and labels as apps/go-peer/metrics.go.

impl and peer are fixed per process, so they are bound once here instead of
being passed on every call. Series names and label sets match the Go peer.
"""
from prometheus_client import (CollectorRegistry, Counter, Gauge, Histogram,
                               GC_COLLECTOR, PLATFORM_COLLECTOR, PROCESS_COLLECTOR)


class Metrics:
    def __init__(self, impl: str, peer: str):
        self.registry = CollectorRegistry()
        for collector in (PROCESS_COLLECTOR, PLATFORM_COLLECTOR, GC_COLLECTOR):
            try:
                self.registry.register(collector)
            except ValueError:
                pass
        base = ["impl", "peer"]
        bound = (impl, peer)

        def counter(name, help_, extra=()):
            return Counter(name, help_, labelnames=base + list(extra), registry=self.registry)

        def gauge(name, help_, extra=()):
            return Gauge(name, help_, labelnames=base + list(extra), registry=self.registry)

        self.publish_attempted = counter("gossip_publish_attempted_total", "Publish calls attempted.").labels(*bound)
        self.publish_success = counter("gossip_publish_success_total", "Publish calls that returned no error.").labels(*bound)
        self.publish_errors = counter("gossip_publish_errors_total", "Publish calls that returned an error.").labels(*bound)
        self._deliveries = counter("gossip_deliveries_total", "Unique application messages received, by publisher.", ["publisher"])
        self.duplicates = counter("gossip_duplicates_total", "Application-level duplicate receipts.").labels(*bound)
        self.decode_errors = counter("gossip_decode_errors_total", "Messages that failed to decode or validate.").labels(*bound)
        self.clock_skew = counter("gossip_clock_skew_events_total", "Receipts whose receive time precedes publish time.").labels(*bound)
        self.delivery_latency = Histogram(
            "gossip_delivery_latency_seconds",
            "Publish-to-receive latency on the application level. Assumes all pods share one host clock.",
            labelnames=base, registry=self.registry,
            buckets=[0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
        ).labels(*bound)
        self.connected_peers = gauge("gossip_connected_peers", "Number of libp2p peers with an open connection.").labels(*bound)
        self._mesh = gauge("gossip_mesh_peers", "GossipSub mesh size per topic.", ["topic"])
        self.dial_errors = counter("gossip_dial_errors_total", "Failed dial attempts from the redial loop.").labels(*bound)
        self.last_publish_ts = gauge("gossip_last_publish_timestamp_seconds", "Unix time of the last successful publish.").labels(*bound)
        self.last_recv_ts = gauge("gossip_last_receive_timestamp_seconds", "Unix time of the last receipt.").labels(*bound)
        build = Gauge("gossip_build_info", "Constant 1, labelled with version info.", labelnames=base + ["version"], registry=self.registry)
        build.labels(*bound, "0.1.0").set(1)
        self._bound = bound

    def deliveries(self, publisher: str):
        return self._deliveries.labels(*self._bound, publisher)

    def mesh_peers(self, topic: str):
        return self._mesh.labels(*self._bound, topic)
