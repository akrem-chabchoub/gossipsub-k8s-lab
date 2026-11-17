# Future enhancements

The lab covers everything the brief asks for. These are the next steps I would take, grouped by area and ranked at
the end. The three with the most value:

1. Repeat each scenario several times and turn the results into automatic pass/fail checks.
2. Cut peers off without restarting their pods.
3. Alert on delivery and on missing telemetry, not only on connectivity, and send alerts to a phone.

## Open findings to explain first

| Finding | Evidence today | Next step |
|---|---|---|
| Messages published during the cut: 36–39% never reach the isolated peers | Full run: go-peer-2 got 64.4%, py-peer-2 got 60.3% | Expected, if confirmed (see below) |
| The messages that do arrive are very late | p50 83–90 s, p99 173 s | Find which peer delivered them, using `fromName` in the receipt logs |
| Run 6 lost nothing during the cut, but the full run lost about 38% | Two runs that disagree | Repeat runs (see Better tests) |
| In the first run, Go peers connected to the isolated peers | Connection counts | Re-test with the conntrack method below, which keeps the same processes |

**Hypothesis for the lost messages (not confirmed):** GossipSub doesn't store messages and replay them later. A peer can
recover a missed message only through gossip: a neighbour announces the message ID (IHAVE), and the peer asks for it
(IWANT). Neighbours announce only IDs from the last few heartbeats, about 3 s at the lab's 1 s heartbeat. A peer that
was cut off for 4 minutes can't recover messages older than that, so most of them never arrive. To check it:

- List the receipts with latency above 5 s, grouped by `fromName` and arrival time. If they all arrive in one burst
  right after recovery, look for a queue or buffer on the sending side.
- Repeat with a longer gossip history (`HistoryLength` and `HistoryGossip` in go-libp2p-pubsub), and check whether
  the share of late messages grows.

## Better tests

- **Repeat runs.** Run each scenario at least 5 times (`RUN=s2-1` … `s2-5`). Report the median and range of delivery,
  latency, time to reconnect, and time for the alert to fire and to clear. One run per scenario can't show the spread.
- **Automatic pass/fail.** Add a `make test-assert RUN=x` step that exits non-zero when a check fails:
  - Baseline: delivery at least 99.9% on every subscriber, p99 below 100 ms.
  - During the cut: probes FAIL for the isolated pair and OK for the controls; the isolated peers drop to 1
    connection and the others to 3.
  - Recovery: every peer back at 5 connections within 60 s; the alert fires and then returns to Normal.
- **Isolation without a restart.** Today the two isolated pods are restarted, because a NetworkPolicy doesn't drop
  connections that are already open. Instead, delete their entries from the kind node's connection-tracking table:

  ```bash
  docker exec gossip-lab-control-plane conntrack -D -s <pod IP>
  ```

  The processes keep running, so their logs cover the whole run and the cut tests only the network. Keep the restart
  method as a fallback and compare the two.
- **More failure shapes:**
  - Cut a Go-only pair, a Python-only pair, and a mixed pair, to separate implementation effects from network effects.
  - Cut the publisher's direct mesh neighbours instead of two arbitrary peers.
  - Split the network 3/3.
  - Block traffic in one direction only (ingress-only policy).
  - Flap the cut (30 s on, 30 s off) to test how GossipSub and the alert handle instability.
  - Add packet loss and delay with `tc netem` (or Chaos Mesh) instead of a hard cut.
- **Workload sweep.** Rates of 1, 10, 50 and 100 msg/s, with payloads of 1, 16 and 64 KiB. Find where py-libp2p starts
  dropping messages, and record CPU and memory at each step.
- **Soak test.** One hour of baseline to catch memory growth and log rotation problems.
- **Tests for the analysis code.** Unit tests for `analyze_phase.py` and `analyze_delivery.py`, using small fixture
  logs, so a change to the analysis can't silently change the results.
- **CI.** A GitHub Actions job that creates the kind cluster, runs a short scenario (2 msg/s, 60 s cut), runs the
  assertions, and uploads `results/` as an artifact.

## Better alerting

### More rules

Today there is one rule: connectivity below 5. Each rule below answers one of the dashboard's questions. The queries
are sketches and need tuning against real runs.

| Alert | Query (sketch) | For | Severity |
|---|---|---|---|
| Delivery incomplete | `sum by (namespace) (rate(gossip_deliveries_total[1m])) / (5 * sum by (namespace) (rate(gossip_publish_success_total[1m]))) < 0.99` | 2m | critical |
| Publisher stalled | `time() - gossip_last_publish_timestamp_seconds > 30` | 1m | critical |
| Subscriber silent | `time() - gossip_last_receive_timestamp_seconds > 30` | 1m | warning |
| Propagation slow | `histogram_quantile(0.99, sum by (le, namespace) (rate(gossip_delivery_latency_seconds_bucket[5m]))) > 1` | 5m | warning |
| Publish errors | `increase(gossip_publish_errors_total[5m]) > 0` | 0m | warning |
| Telemetry missing | `up == 0` or `absent(gossip_connected_peers{namespace="gossip-mixed"})` | 2m | critical |
| Pod restarting | `increase(kube_pod_container_status_restarts_total{namespace=~"gossip-.*"}[10m]) > 0` | 0m | warning |
| CPU throttled | `rate(container_cpu_cfs_throttled_periods_total[5m]) / rate(container_cpu_cfs_periods_total[5m]) > 0.25` | 5m | warning |
| Mesh too small | `gossip_mesh_peers < 4` (set from the mesh size seen in a healthy baseline) | 2m | warning |

### Better rules

- **Name the affected peer.** The current rule groups by `namespace`, so a notification says that the mixed network is
  degraded, but not which peers. Grouping by `namespace, peer` names go-peer-2 and py-peer-2. Group them in the
  notification policy so one cut still sends one message.
- **Treat missing data as a problem.** The current rule uses `noDataState: NoData`. For connectivity, no data usually
  means a peer is down or isn't being scraped, so `Alerting` is the safer choice.
- **Runbooks and links.** Add a `runbook_url` annotation pointing to a README section that says what to check, plus
  the dashboard and panel IDs, so the notification opens the right panel.
- **Expected alerts during experiments.** The scenario scripts can label the time window (or create a Grafana
  annotation), so an alert during a planned cut is easy to tell apart from a real one.

### Send alerts to a phone

Today, alerts only show in Grafana. No contact point is configured.

- **Telegram:** a contact point built into Grafana. Keep the bot token and chat ID in a Kubernetes Secret, created from
  a git-ignored env file and loaded with the chart's `envFromSecrets`, and provision the contact point and the
  notification policy from a separate values file used only when that file exists. Note that the chart passes alerting
  values through Helm's `tpl`, so Grafana's own `{{ }}` message templates must be escaped.
- **ntfy:** a webhook to an ntfy topic, read in the ntfy phone app. No account is needed.
- **Grafana Cloud IRM:** the hosted on-call product, for escalations and acknowledging from the phone. Self-hosted
  Grafana OnCall (OSS) is archived, and its mobile push relay stopped working on 2026-03-24
  ([notice](https://grafana.com/docs/oncall/latest/set-up/open-source/)).

### Test the alerts

- Turn `alert_state.py`'s samples into assertions: the alert fires within 90 s of the cut and returns to Normal within
  90 s of recovery.
- Alternatively, move the rules to Prometheus alerting rules with Alertmanager (currently disabled in
  `prometheus-values.yaml`). Rules can then be unit-tested offline with `promtool test rules`, using synthetic series
  for a cut and a recovery, with no cluster needed.

## Better observability

- **Mark the cut on the dashboard.** `isolate.sh` and `recover.sh` post Grafana annotations through the API, so every
  panel shows when the cut started and ended.
- **Logs in Grafana.** Add the VictoriaLogs datasource plugin, preloaded like the other images. The per-message
  timeline then becomes a live panel instead of a query to copy.
- **Recording rules.** Precompute delivery completeness and the latency quantiles. Panels and alerts then use the same
  definition, and they load faster.
- **More GossipSub internals.** GRAFT, PRUNE, IHAVE and IWANT counters from go-libp2p-pubsub's tracer, to show the mesh
  repairing itself during recovery. Check what py-libp2p exposes before adding them to the shared metric contract.
- **Log volume.** Every receipt is logged, and peer logs rotate at 10 MiB. Sample the receipt logs, or raise the
  rotation size for longer runs.

## Reproducibility

- **Pin images by digest:** `kindest/node`, Calico, and the chart images, not only by tag.
- **Linux support:** run `caffeinate` only when it exists (`command -v caffeinate`), so the test targets work on
  Linux unchanged.
- **Prebuilt peer images** on a registry (for example GHCR), so `make up` doesn't depend on Docker Desktop's DNS
  during the build.
- **A multi-node kind cluster**, so traffic crosses nodes and Calico's routing is part of the test. Latency then
  needs clock-skew checks between nodes; `gossip_clock_skew_events_total` already exists for that.
- **Hash-pinned Python dependencies** (`pip install --require-hashes`).

## Priority

| # | Enhancement | Value | Effort |
|---|---|---|---|
| 1 | Pass/fail assertions plus 5 repeat runs per scenario | High: turns single runs into evidence | Medium |
| 2 | Explain the late and lost messages (`fromName` query, gossip history test) | High: closes the open finding | Low |
| 3 | Isolation with conntrack instead of a pod restart | High: cleaner experiment, full logs | Low |
| 4 | Delivery, telemetry-missing and per-peer alerts | High: alerts on what users feel | Low |
| 5 | Alerts to a phone (Telegram or ntfy) | Medium | Low |
| 6 | Dashboard annotations for the cut | Medium: makes screenshots self-explaining | Low |
| 7 | More failure shapes and the workload sweep | Medium | Medium |
| 8 | CI on GitHub Actions | Medium: catches regressions | Medium |
| 9 | Alert rules tested with `promtool` | Medium | Medium |
| 10 | Multi-node cluster, image digests, Linux support | Low for this lab | Medium |
