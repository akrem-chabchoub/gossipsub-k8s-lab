// Command go-peer is one GossipSub participant in the observability lab.
// It subscribes to a shared topic, optionally publishes at a configurable
// rate, exports Prometheus metrics, and writes one JSON log line per receipt.
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/libp2p/go-libp2p"
	pubsub "github.com/libp2p/go-libp2p-pubsub"
	pb "github.com/libp2p/go-libp2p-pubsub/pb"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/libp2p/go-libp2p/p2p/muxer/yamux"
	"github.com/libp2p/go-libp2p/p2p/security/noise"
	"github.com/libp2p/go-libp2p/p2p/transport/tcp"
	"github.com/multiformats/go-multiaddr"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

const version = "0.1.0"

// Config is read from environment variables (see README for the table).
type Config struct {
	Name           string        // stable peer name, also the DNS host label
	Impl           string        // "go"
	Topic          string        // GossipSub topic
	RunID          string        // run identifier stamped on every message
	Publisher      bool          // true if this peer publishes (PUBLISHER_NAME == PEER_NAME)
	PublishRate    float64       // messages per second (publisher only)
	PayloadBytes   int           // approximate payload size in bytes
	PublishFor     time.Duration // 0 = publish until stopped
	PeerNames      []string      // all peer names in the network, including self
	DNSSuffix      string        // appended to peer name to form the DNS host
	ListenPort     int           // libp2p TCP port
	MetricsPort    int           // HTTP port for /metrics, /healthz, /livez
	RedialInterval time.Duration // how often to re-dial missing peers
}

func loadConfig() (Config, error) {
	c := Config{
		Impl:           "go",
		Topic:          getenv("TOPIC", "lab/v1"),
		RunID:          getenv("RUN_ID", "run-unset"),
		DNSSuffix:      getenv("PEER_DNS_SUFFIX", ""),
		ListenPort:     getenvInt("LISTEN_PORT", 4001),
		MetricsPort:    getenvInt("METRICS_PORT", 9100),
		PayloadBytes:   getenvInt("PAYLOAD_BYTES", 1024),
		PublishRate:    getenvFloat("PUBLISH_RATE", 10),
		PublishFor:     time.Duration(getenvInt("PUBLISH_FOR_SECONDS", 0)) * time.Second,
		RedialInterval: time.Duration(getenvInt("REDIAL_INTERVAL_SECONDS", 5)) * time.Second,
	}
	name := getenv("PEER_NAME", "")
	if name == "" {
		h, err := os.Hostname()
		if err != nil {
			return c, err
		}
		name = h
	}
	c.Name = name
	c.PeerNames = splitNonEmpty(getenv("PEER_NAMES", name))
	c.Publisher = name == getenv("PUBLISHER_NAME", "")
	if c.RunID == "run-unset" {
		c.RunID = time.Now().UTC().Format("20060102T150405Z")
	}
	if c.PublishRate <= 0 {
		return c, errors.New("PUBLISH_RATE must be > 0")
	}
	if c.PayloadBytes < 0 || c.PayloadBytes > 64*1024 {
		return c, errors.New("PAYLOAD_BYTES must be between 0 and 65536")
	}
	return c, nil
}

// traceAdapter forwards GossipSub trace events into the mesh tracker and logs.
type traceAdapter struct {
	mesh  *MeshTracker
	log   *slog.Logger
	names map[string]string // peer ID -> peer name
}

func (a *traceAdapter) Trace(evt *pb.TraceEvent) {
	switch evt.GetType() {
	case pb.TraceEvent_GRAFT:
		g := evt.GetGraft()
		p := a.nameOf(peer.ID(g.GetPeerID()).String())
		a.mesh.Graft(g.GetTopic(), p)
		a.log.Info("graft", "event", "graft", "topic", g.GetTopic(), "peerName", p)
	case pb.TraceEvent_PRUNE:
		pr := evt.GetPrune()
		p := a.nameOf(peer.ID(pr.GetPeerID()).String())
		a.mesh.Prune(pr.GetTopic(), p)
		a.log.Info("prune", "event", "prune", "topic", pr.GetTopic(), "peerName", p)
	}
}

func (a *traceAdapter) nameOf(id string) string {
	if n, ok := a.names[id]; ok {
		return n
	}
	return id
}

func main() {
	cfg, err := loadConfig()
	if err != nil {
		fmt.Fprintln(os.Stderr, "config:", err)
		os.Exit(2)
	}
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil)).With(
		"impl", cfg.Impl, "peer", cfg.Name)
	if err := run(cfg, logger); err != nil {
		logger.Error("fatal", "event", "fatal", "err", err.Error())
		os.Exit(1)
	}
}

func run(cfg Config, logger *slog.Logger) error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	priv, selfID, err := identityFor(cfg.Name)
	if err != nil {
		return err
	}

	// Map every known peer ID to its name, so logs show names not hashes.
	names := make(map[string]string, len(cfg.PeerNames))
	targets := make(map[peer.ID]multiaddr.Multiaddr)
	for _, n := range cfg.PeerNames {
		_, id, err := identityFor(n)
		if err != nil {
			return err
		}
		names[id.String()] = n
		// Only the lexicographically smaller name dials each pair (same rule as py-peer).
		// Dialing from both sides at once caused handshake failures and churn in py-libp2p.
		if n <= cfg.Name {
			continue
		}
		addr, err := multiaddr.NewMultiaddr(fmt.Sprintf("/dns4/%s%s/tcp/%d", n, cfg.DNSSuffix, cfg.ListenPort))
		if err != nil {
			return err
		}
		targets[id] = addr
	}

	reg := prometheus.NewRegistry()
	reg.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	m := NewMetrics(reg, cfg.Impl, cfg.Name)
	m.BuildInfo.WithLabelValues(version).Set(1)
	mesh := NewMeshTracker(m.MeshPeers)
	tracer := &traceAdapter{mesh: mesh, log: logger, names: names}

	h, err := libp2p.New(
		libp2p.Identity(priv),
		libp2p.ListenAddrStrings(fmt.Sprintf("/ip4/0.0.0.0/tcp/%d", cfg.ListenPort)),
		libp2p.Transport(tcp.NewTCPTransport),
		libp2p.Security(noise.ID, noise.New),
		libp2p.Muxer(yamux.ID, yamux.DefaultTransport),
	)
	if err != nil {
		return fmt.Errorf("libp2p host: %w", err)
	}
	defer h.Close()

	h.Network().Notify(&network.NotifyBundle{
		ConnectedF: func(_ network.Network, c network.Conn) {
			logger.Info("connect", "event", "connect", "peerName", nameOrID(names, c.RemotePeer()))
		},
		DisconnectedF: func(_ network.Network, c network.Conn) {
			mesh.RemovePeer(nameOrID(names, c.RemotePeer()))
			logger.Info("disconnect", "event", "disconnect", "peerName", nameOrID(names, c.RemotePeer()))
		},
	})

	ps, err := pubsub.NewGossipSub(ctx, h, pubsub.WithEventTracer(tracer))
	if err != nil {
		return fmt.Errorf("gossipsub: %w", err)
	}
	topic, err := ps.Join(cfg.Topic)
	if err != nil {
		return fmt.Errorf("join topic: %w", err)
	}
	sub, err := topic.Subscribe()
	if err != nil {
		return fmt.Errorf("subscribe: %w", err)
	}

	var ready atomic.Bool
	ready.Store(true)
	go serveHTTP(cfg.MetricsPort, reg, &ready, logger)
	go sampleConnections(ctx, h, m)
	go redialLoop(ctx, h, targets, cfg.RedialInterval, m, logger)

	dedupe := NewDeduper()
	go receiveLoop(ctx, sub, selfID, dedupe, m, names, logger)

	if cfg.Publisher {
		go publishLoop(ctx, cfg, topic, selfID, m, logger)
	}

	logger.Info("started", "event", "started", "publisher", cfg.Publisher, "runId", cfg.RunID, "peerId", selfID.String(),
		"listenPort", cfg.ListenPort, "targets", len(targets))
	<-ctx.Done()
	logger.Info("stopping", "event", "stopping")
	return nil
}

func receiveLoop(ctx context.Context, sub *pubsub.Subscription, selfID peer.ID,
	dedupe *Deduper, m *Metrics, names map[string]string, logger *slog.Logger) {
	for {
		msg, err := sub.Next(ctx)
		if err != nil {
			return
		}
		pm, err := DecodeMessage(msg.Data)
		if err != nil {
			m.DecodeErrors.Inc()
			logger.Warn("decode_error", "event", "decode_error", "err", err.Error())
			continue
		}
		if pm.PublisherID == selfID.String() {
			continue // own publications are not deliveries
		}
		recvNs := time.Now().UnixNano()
		if dedupe.Seen(pm.PublisherID, pm.Seq) {
			m.Duplicates.Inc()
			continue
		}
		latency, ok := LatencySeconds(pm.PublishedNs, recvNs)
		if !ok {
			m.ClockSkew.Inc()
		}
		m.DeliveryLatency.Observe(latency)
		m.Deliveries.WithLabelValues(names[pm.PublisherID]).Inc()
		m.LastRecvTS.SetToCurrentTime()
		logger.Info("receive", "event", "receive", "runId", pm.RunID, "publisherId", pm.PublisherID,
			"publisherName", names[pm.PublisherID], "seq", pm.Seq,
			"publishedAtNs", pm.PublishedNs, "receivedAtNs", recvNs,
			"latencyMs", latency*1000, "fromName", names[msg.ReceivedFrom.String()])
	}
}

func publishLoop(ctx context.Context, cfg Config, topic *pubsub.Topic, selfID peer.ID,
	m *Metrics, logger *slog.Logger) {
	// Wait for at least one mesh peer before publishing.
	for len(topic.ListPeers()) == 0 {
		select {
		case <-ctx.Done():
			return
		case <-time.After(200 * time.Millisecond):
		}
	}
	logger.Info("publish_start", "event", "publish_start", "runId", cfg.RunID, "rate", cfg.PublishRate,
		"payloadBytes", cfg.PayloadBytes)
	payload := MakePayload(cfg.PayloadBytes)
	interval := time.Duration(float64(time.Second) / cfg.PublishRate)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	var deadline <-chan time.Time
	if cfg.PublishFor > 0 {
		deadline = time.After(cfg.PublishFor)
	}
	seq := 0
	for {
		select {
		case <-ctx.Done():
			return
		case <-deadline:
			logger.Info("publish_done", "event", "publish_done", "runId", cfg.RunID, "lastSeq", seq)
			return
		case <-ticker.C:
			seq++
			m.PublishAttempted.Inc()
			msg := Message{RunID: cfg.RunID, PublisherID: selfID.String(), Seq: seq,
				PublishedNs: time.Now().UnixNano(), Impl: cfg.Impl, Payload: payload}
			b, err := msg.Encode()
			if err == nil {
				err = topic.Publish(ctx, b)
			}
			if err != nil {
				m.PublishErrors.Inc()
				logger.Warn("publish_error", "event", "publish_error", "seq", seq, "err", err.Error())
				continue
			}
			m.PublishSuccess.Inc()
			m.LastPublishTS.SetToCurrentTime()
			logger.Info("publish", "event", "publish", "runId", cfg.RunID, "seq", seq, "publishedAtNs", msg.PublishedNs)
		}
	}
}

// redialLoop keeps connections to every other peer. libp2p does not reconnect
// on its own, so this loop is what makes recovery after isolation possible.
func redialLoop(ctx context.Context, h interface {
	Network() network.Network
	Connect(context.Context, peer.AddrInfo) error
}, targets map[peer.ID]multiaddr.Multiaddr, every time.Duration, m *Metrics, logger *slog.Logger) {
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		for id, addr := range targets {
			if h.Network().Connectedness(id) == network.Connected {
				continue
			}
			dctx, cancel := context.WithTimeout(ctx, 4*time.Second)
			err := h.Connect(dctx, peer.AddrInfo{ID: id, Addrs: []multiaddr.Multiaddr{addr}})
			cancel()
			if err != nil {
				m.DialErrors.Inc()
				logger.Warn("dial_error", "event", "dial_error", "peerId", id.String(), "err", err.Error())
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

func sampleConnections(ctx context.Context, h interface {
	Network() network.Network
}, m *Metrics) {
	t := time.NewTicker(time.Second)
	defer t.Stop()
	for {
		m.ConnectedPeers.Set(float64(len(h.Network().Peers())))
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

func serveHTTP(port int, reg *prometheus.Registry, ready *atomic.Bool, logger *slog.Logger) {
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.HandlerFor(reg, promhttp.HandlerOpts{}))
	mux.HandleFunc("/livez", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		if ready.Load() {
			w.WriteHeader(http.StatusOK)
			return
		}
		w.WriteHeader(http.StatusServiceUnavailable)
	})
	srv := &http.Server{Addr: ":" + strconv.Itoa(port), Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		logger.Error("http", "event", "http_error", "err", err.Error())
	}
}

func nameOrID(names map[string]string, id peer.ID) string {
	if n, ok := names[id.String()]; ok {
		return n
	}
	return id.String()
}

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func getenvInt(k string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(k)); err == nil {
		return v
	}
	return def
}

func getenvFloat(k string, def float64) float64 {
	if v, err := strconv.ParseFloat(os.Getenv(k), 64); err == nil {
		return v
	}
	return def
}

func splitNonEmpty(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}
