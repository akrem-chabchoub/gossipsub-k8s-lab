package main

import (
	"sync"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Metrics holds all application metrics. Constant labels impl and peer are
// attached at registration so every series is attributable without relabeling.
type Metrics struct {
	PublishAttempted prometheus.Counter
	PublishSuccess   prometheus.Counter
	PublishErrors    prometheus.Counter

	Deliveries      *prometheus.CounterVec // label: publisher
	Duplicates      prometheus.Counter
	DecodeErrors    prometheus.Counter
	ClockSkew       prometheus.Counter
	DeliveryLatency prometheus.Histogram

	ConnectedPeers prometheus.Gauge
	MeshPeers      *prometheus.GaugeVec // label: topic
	DialErrors     prometheus.Counter

	LastPublishTS prometheus.Gauge
	LastRecvTS    prometheus.Gauge
	BuildInfo     *prometheus.GaugeVec
}

// NewMetrics registers all metrics on reg with impl and peer constant labels.
func NewMetrics(reg prometheus.Registerer, impl, peer string) *Metrics {
	w := prometheus.WrapRegistererWith(prometheus.Labels{"impl": impl, "peer": peer}, reg)
	f := promauto.With(w)
	m := &Metrics{
		PublishAttempted: f.NewCounter(prometheus.CounterOpts{Name: "gossip_publish_attempted_total", Help: "Publish calls attempted."}),
		PublishSuccess:   f.NewCounter(prometheus.CounterOpts{Name: "gossip_publish_success_total", Help: "Publish calls that returned no error."}),
		PublishErrors:    f.NewCounter(prometheus.CounterOpts{Name: "gossip_publish_errors_total", Help: "Publish calls that returned an error."}),
		Deliveries: f.NewCounterVec(prometheus.CounterOpts{Name: "gossip_deliveries_total", Help: "Unique application messages received, by publisher."},
			[]string{"publisher"}),
		Duplicates:   f.NewCounter(prometheus.CounterOpts{Name: "gossip_duplicates_total", Help: "Application-level duplicate receipts."}),
		DecodeErrors: f.NewCounter(prometheus.CounterOpts{Name: "gossip_decode_errors_total", Help: "Messages that failed to decode or validate."}),
		ClockSkew:    f.NewCounter(prometheus.CounterOpts{Name: "gossip_clock_skew_events_total", Help: "Receipts whose receive time precedes publish time (clock disagreement)."}),
		DeliveryLatency: f.NewHistogram(prometheus.HistogramOpts{
			Name:    "gossip_delivery_latency_seconds",
			Help:    "Publish-to-receive latency on the application level. Assumes all pods share one host clock.",
			Buckets: []float64{0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5},
		}),
		ConnectedPeers: f.NewGauge(prometheus.GaugeOpts{Name: "gossip_connected_peers", Help: "Number of libp2p peers with an open connection."}),
		MeshPeers: f.NewGaugeVec(prometheus.GaugeOpts{Name: "gossip_mesh_peers", Help: "GossipSub mesh size per topic, derived from GRAFT/PRUNE trace events."},
			[]string{"topic"}),
		DialErrors:    f.NewCounter(prometheus.CounterOpts{Name: "gossip_dial_errors_total", Help: "Failed dial attempts from the redial loop."}),
		LastPublishTS: f.NewGauge(prometheus.GaugeOpts{Name: "gossip_last_publish_timestamp_seconds", Help: "Unix time of the last successful publish."}),
		LastRecvTS:    f.NewGauge(prometheus.GaugeOpts{Name: "gossip_last_receive_timestamp_seconds", Help: "Unix time of the last receipt."}),
		BuildInfo: f.NewGaugeVec(prometheus.GaugeOpts{Name: "gossip_build_info", Help: "Constant 1, labelled with version info."},
			[]string{"version"}),
	}
	return m
}

// MeshTracker maintains mesh membership per topic from GRAFT/PRUNE events.
type MeshTracker struct {
	mu     sync.Mutex
	mesh   map[string]map[string]struct{} // topic -> peer set
	metric *prometheus.GaugeVec
}

func NewMeshTracker(metric *prometheus.GaugeVec) *MeshTracker {
	return &MeshTracker{mesh: make(map[string]map[string]struct{}), metric: metric}
}

func (t *MeshTracker) Graft(topic, peer string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.mesh[topic] == nil {
		t.mesh[topic] = make(map[string]struct{})
	}
	t.mesh[topic][peer] = struct{}{}
	t.metric.WithLabelValues(topic).Set(float64(len(t.mesh[topic])))
}

func (t *MeshTracker) Prune(topic, peer string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if set, ok := t.mesh[topic]; ok {
		delete(set, peer)
		t.metric.WithLabelValues(topic).Set(float64(len(set)))
	}
}

// RemovePeer drops a peer from every topic's mesh (used on disconnect).
func (t *MeshTracker) RemovePeer(peer string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	for topic, set := range t.mesh {
		if _, ok := set[peer]; ok {
			delete(set, peer)
			t.metric.WithLabelValues(topic).Set(float64(len(set)))
		}
	}
}
