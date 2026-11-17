# Report: GossipSub on kind, go-libp2p and py-libp2p

Six peers, three Go and three Python, share one GossipSub topic on a single-node kind cluster. `go-peer-0` publishes
10 msg/s with 1 KiB payloads. Two non-publishing peers, `go-peer-2` and `py-peer-2`, are cut off from the other four
with a NetworkPolicy for about 4.5 minutes and then reconnected.

**Headline results**

- **Baseline:** 100% delivery to every subscriber in both networks.
  - Go subscribers: p50 about 1 ms, p99 under 10 ms.
  - Python subscribers: p50 about 4 ms, p99 under 30 ms.
- **During the cut:** the isolated pair received nothing from the rest of the network (0% on time), and the other
  four stayed at 100%. Connection counts dropped to 1 on the isolated peers and 3 on the others.
- **Recovery:** both peers reconnected to all four others within about 1.5 s of the policy being removed, and new
  messages were back to 100%.
- **Missed messages:** about 60% of the messages published during the cut arrived in one burst 1–6 s after recovery.
  The other 40%, the oldest part of the cut, never arrived. The burst is most likely caused by py-libp2p's adaptive
  heartbeat. The mechanism is in the library source but not yet measured
  ([Section 4.3](#43-why-some-missed-messages-arrive-after-recovery-and-the-rest-never-do)).

Evidence is in `results/<run>/`. That directory is git-ignored, so the numbers and excerpts below are copied from it,
and the commands in [Section 8](#8-reproduce) regenerate everything.

## 1. Implementations and versions

| Role | Implementation | Version |
|---|---|---|
| Primary | go-libp2p + go-libp2p-pubsub (GossipSub v1.1), Go 1.26.6 | v0.51.0 / v0.19.0 |
| Second | py-libp2p (`/meshsub/1.1.0`), Python 3.12, trio | 0.8.1 |

Images: `gossip/go-peer:0.1.2`, `gossip/py-peer:0.1.6`.

Telemetry:

| Component | Chart version | App version |
|---|---|---|
| Prometheus | 29.47.2 | v3.16.0 |
| Grafana | 10.5.15 | 12.3.1 |
| VictoriaLogs | 0.13.13 | v1.54.0 |
| Vector | 0.61.0 | |

Cluster: kind with `kindest/node:v1.34.0` and Calico v3.30.3. The default CNI is disabled because kindnet does not
enforce NetworkPolicy.

## 2. Measurement assumptions

- **Run identity.** Every message carries `runId`, `publisherId`, `seq` and `publishedAtNs`.
  - Each subscriber counts a message once per `(runId, publisherId, seq)`.
  - The run ID is part of that key because `seq` restarts at 1 when a publisher restarts.
- **Latency** is `receivedAtNs − publishedAtNs`, measured by the applications.
  - The publish timestamp is taken immediately before `Publish`. The receive timestamp is taken when the
    application reads the message from its subscription.
  - **One host clock:** all pods run on one kind node, so the two timestamps come from the same clock. The numbers
    are comparable within this setup only.
  - A negative difference is counted in `gossip_clock_skew_events_total` and is not added to the histogram. None
    occurred in these runs.
- **Delivery numbers come from the logs.** Every receipt is a JSON log line, and `scripts/analyze_phase.py`
  assigns each message to a phase by its publish time. For each subscriber it reports:
  - `on_time`: received by the end of the phase plus a grace period.
  - `late`: received after that, with the time window the late receipts arrived in.
  - `never`: not received at all.
  - The isolation phase uses a grace of 0. A message published during the cut that arrives after recovery
    therefore counts as late, not on time.
- **Percentiles** in the tables are nearest-rank values over the logged receipts. The dashboard's p50/p95/p99 come
  from the Prometheus histogram (buckets from 1 ms to 5 s), so they are coarser.
- **Completeness on the dashboard** compares deliveries with publish rate × (desired StatefulSet replicas − 1). The
  expected count doesn't shrink when a peer is isolated or stops being scraped (D3).

## 3. Baseline (scenario 1)

Method (`make test-scenario1`):

1. Wait until every peer reports 5 connections.
2. Let the mesh settle for 60 s.
3. Measure for 300 s, then wait 10 s for late receipts.

The completeness and latency thresholds were set before the first baseline run (D2): completeness at least 99.9%,
p99 at most 250 ms.

| Network (run) | Window (UTC) | Published | Subscriber | On time | p50 / p95 / p99 (ms) |
|---|---|---|---|---|---|
| Mixed (`report-baseline-mixed`) | 11:51:49–11:56:49 | 2999 | go-peer-1 | 100% | 1.06 / 3.76 / 9.45 |
| | | | go-peer-2 | 100% | 1.01 / 3.58 / 9.72 |
| | | | py-peer-0 | 100% | 4.20 / 11.22 / 26.86 |
| | | | py-peer-1 | 100% | 4.23 / 11.11 / 28.06 |
| | | | py-peer-2 | 100% | 4.24 / 11.40 / 25.56 |
| Six Go (`report-baseline-go`) | 11:58:01–12:03:01 | 2998 | go-peer-1 … go-peer-5 | 100% each | p50 0.97–1.10 / p95 2.98–3.27 / p99 7.50–9.39 |

Both networks meet both thresholds.

Python subscribers are about 4× slower at the median. The Go figures are similar in both networks, so the extra time
is spent inside the Python peer. It covers py-libp2p's receive path, the trio scheduler, and JSON decoding. This is a
hypothesis: I have not profiled the Python peer.

## 4. Isolation and recovery (scenario 2)

### 4.1 Method

- **Policy:** `deploy/k8s/mixed/isolate-go2-py2.yaml` is one NetworkPolicy, labelled
  `gossip.lab/experiment=isolation-recovery`.
  - It selects `go-peer-2` and `py-peer-2` for both ingress and egress.
  - It allows traffic only between those two pods, to and from the `observability` namespace (so Prometheus scrapes
    keep working), and DNS to `kube-system`.
  - Vector reads log files on the node, so log shipping does not cross the policy.
- **Apply:** `scripts/isolate.sh` applies the policy. The scenario script then saves the isolated pods' logs and
  deletes the two pods, so the StatefulSets recreate them under the policy.
  - The restart is needed because the policy alone does not drop established TCP connections. An earlier run
    without the restart kept 5 connections and 100% delivery for the whole cut (D11).
- **Remove:** `scripts/recover.sh` deletes only the labelled policy.
- **Timing:** every step is timestamped in `results/<run>/events.log`.

  ```
  2026-11-17T12:05:23Z isolation_policy_applied
  2026-11-17T12:05:25Z isolated_pods_deleted
  2026-11-17T12:05:32Z isolated_pods_ready_under_policy
  2026-11-17T12:10:00Z isolation_policy_removed
  ```

One command runs the whole scenario: `make test-scenario2 RUN=<name>`. The phases are 120 s before the cut, 240 s
of probes during it, and 150 s of recovery.

### 4.2 Results

Run `report-isolation` (12:03–12:12 UTC). Delivery per phase, by publish time:

| Phase (publish window) | Published | go-peer-1, py-peer-0, py-peer-1 | go-peer-2 | py-peer-2 |
|---|---|---|---|---|
| Pre-isolation (12:03:23–12:05:23) | 1200 | 100% on time | 100% on time | 100% on time |
| Pods restarting (12:05:23–12:05:32) | 90 | 100% | 9 on time, 81 never | 12 on time, 78 never |
| Isolation (12:05:32–12:10:00) | 2679 | 100% on time | **0 on time, 1603 late, 1076 never** | **0 on time, 1615 late, 1064 never** |
| Recovery (12:10:00–12:12:31) | 1510 | 100% on time | 100% on time | 100% on time |

Late arrivals came in a single burst: 12:10:00.986–12:10:02.377 for go-peer-2, and 12:10:00.982–12:10:05.676 for
py-peer-2.

Latency for the non-isolated subscribers stayed at baseline levels during the cut. For example, go-peer-1 had a
p99 of 7.63 ms.

**Connectivity** (Prometheus `gossip_connected_peers` for the current pods' IPs only, see Section 7, and the peers'
connect logs):

- During the cut, `go-peer-2` and `py-peer-2` had 1 connection each (to each other). The other four had 3.
- After the policy was removed, `go-peer-2` logged connections to go-peer-1, go-peer-0, py-peer-1 and py-peer-0
  between 12:10:01.244 and 12:10:01.589. py-peer-2 logged the same four connections at 12:10:01. All six peers were
  back at 5 connections.

**Isolation verified beyond the policy object.** `scripts/verify_isolation.sh` makes real TCP connections to port
4001:

- from a `busybox` probe pod outside the cut, into the cut;
- from `py-peer-2`, out of the cut and to its partner.

Every result is compared with what the current state should allow, and the script exits non-zero on any mismatch.
All 56 checks during the cut and all 7 after it passed (`verify_during.txt`, `verify_after.txt`).

```
12:05:36 PASS probe -> go-peer-2 (192.168.44.101:4001): FAIL expected FAIL
12:05:39 PASS probe -> py-peer-2 (192.168.44.102:4001): FAIL expected FAIL
12:05:43 PASS py-peer-2 -> go-peer-0 (192.168.44.91:4001): FAIL expected FAIL
12:05:46 PASS py-peer-2 -> py-peer-1 (192.168.44.92:4001): FAIL expected FAIL
12:05:46 PASS py-peer-2 -> go-peer-2 (192.168.44.101:4001): OK expected OK
12:05:46 PASS probe -> py-peer-0 (192.168.44.93:4001): OK expected OK
12:05:46 PASS probe -> go-peer-1 (192.168.44.89:4001): OK expected OK
12:10:26 PASS probe -> go-peer-2 (192.168.44.101:4001): OK expected OK      (after recovery; all 7 OK)
```

**Telemetry during the cut.**

- Prometheus kept scraping both isolated peers. `up` stayed at 1, and `prometheus.csv` has their connection counts
  for every 5 s step of the cut.
- Their receipt logs reached VictoriaLogs through Vector.

**Repeat runs.** The pattern is the same in every run:

| Run | Images | Isolation phase, per isolated peer |
|---|---|---|
| `full` (earlier, re-analyzed with the current script) | go-peer 0.1.1 / py-peer 0.1.5 | 0 on time, 1733 late in a burst at 08:34:59–08:35:09, 926 never |
| `report-isolation` | go-peer 0.1.2 / py-peer 0.1.6 | see the table above |
| `report-isolation-2` | go-peer 0.1.2 / py-peer 0.1.6 | RUN2_ISOLATION |

### 4.3 Why some missed messages arrive after recovery, and the rest never do

**What the logs show (confirmed).** In `report-isolation` the cut covered seq 9806–12484.

- After recovery, both isolated peers received messages only from one range, seq 10870–12484, which is the last
  161 s of the cut. py-peer-2 received all 1615 of them. go-peer-2 received 1603; 12 messages inside the range never
  reached it (seq 11807–11808, 12247–12251, 12253, 12320–12323).
- The first 107 s of the cut (seq 9806–10869, 1064 messages) never arrived.
- The oldest backfilled message was 164 s old when it arrived.
- Python peers sent most of the burst. For go-peer-2, `fromName` (the peer that forwarded each message) was:

  | Forwarded by | Messages |
  |---|---|
  | py-peer-0 | 1064 |
  | py-peer-1 | 359 |
  | py-peer-2 | 180 |
  | go-peer-0 or go-peer-1 | 0 |

**Mechanism (confirmed in the library source, not measured directly).** py-libp2p 0.8.1 has an "adaptive gossip"
feature, enabled by default (`adaptive_gossip_enabled=True`). Each heartbeat it can override the configured
heartbeat interval with one based on a network-health score
(`libp2p/pubsub/gossipsub.py`, `_adapt_heartbeat_parameters`):

```python
base = getattr(self, "heartbeat_interval_base", self.heartbeat_interval)   # 1 s in this lab
if health < 0.4:   self.heartbeat_interval = max(30, min(60, base // 2))   # -> 30 s
elif health < 0.7: self.heartbeat_interval = max(60, int(base * 0.75))     # -> 60 s
else:              self.heartbeat_interval = base                          # -> 1 s
```

The message cache moves forward one step per heartbeat (`mcache.shift()`). It keeps 5 steps and gossips the most
recent 3. In normal conditions, at 1 s per heartbeat, that is a few seconds of messages. Then:

1. The cut costs py-peer-0 and py-peer-1 two of their five connections, so their health score drops.
2. Their heartbeat slows to 30–60 s, and their cache stretches to cover minutes.
3. When the isolated peers reconnect, py-libp2p's `send_recent_messages` pushes the cached window to each newly
   subscribed peer. This is the burst.
4. Messages older than the window had already left the cache, so they are lost.

The go-libp2p peers keep a 1 s heartbeat and send nothing old.

**Not yet confirmed.** The heartbeat interval itself is not exported, so the 30–60 s values are inferred from the
code and from the size of the window. The test is to rerun with `adaptive_gossip_enabled=False`: the backfilled block
should shrink to a few seconds of messages.

**What this means.**

- GossipSub has no replay guarantee. A peer that is cut off for minutes loses what was published during the cut,
  except what a neighbour still has cached.
- The amount recovered here depends on a py-libp2p feature that overrides the lab's 1 s heartbeat setting. A
  Go-only network would be expected to backfill only about 3 s worth of messages, but I haven't measured that.

## 5. Alert

The rule is **"Peer connectivity below 5"**:

- It is provisioned from `observability/helm/grafana-values.yaml` in the folder "GossipSub Lab".
- Expression: `min by (namespace) (gossip_connected_peers{namespace=~"gossip-lab|gossip-mixed"})` below 5, held for
  60 s and evaluated every 10 s.
- It uses the reducer `last` (D13). The earlier `min` over the 5-minute window kept the alert firing for up to
  5 minutes after recovery.

During a scenario-2 run, `scripts/alert_state.py` records the rule's state every 20 s to `alert_states.jsonl`.

ALERT_EVIDENCE

There is no contact point, so the alert is visible in Grafana only.

## 6. Mixed-network integration (time-boxed second implementation)

**Result: integration works.** The six peers form one mesh on one topic, and messages travel between
implementations in both directions:

- **Go to Python:** in the mixed baseline, the Go publisher's 2999 messages reached all three Python peers, 100%
  on time.
- **Python to Go, and Go to Python beyond the publisher:** in the 300 s baseline window, `fromName` (the peer that
  delivered each message first) shows:
  - go-peer-1 and go-peer-2 received 11 and 8 messages first from a Python peer;
  - the Python peers received 58–99 messages each first from go-peer-1 and 58–99 from go-peer-2.

  The publisher is directly meshed with every peer, so most first copies come from it. After the cut, go-peer-2
  received 1423 backfilled messages from py-peer-0 and py-peer-1.
- **Before Kubernetes:** a localhost check passed 20 of 20 messages Go to Python and 20 of 20 Python to Go. That
  result is from the author's notes; its logs were not kept.
- **Discovery:** both implementations derive the same peer ID from a peer's name, and they find each other through
  the headless Service `gossip-peers`. No pod IP appears in any manifest.

**Per-message evidence (LogsQL, `observability/queries/logsql.md`).** This is message seq 5000 of the mixed baseline,
publisher first:

```
_time:2h runId:20261117T114911Z seq:5000 (event:receive or event:publish)
  | fields _time, peer, impl, event, latencyMs, fromName

{"peer":"go-peer-0","impl":"go","event":"publish","_time":"2026-11-17T11:57:31.339506631Z"}
{"peer":"go-peer-2","impl":"go","event":"receive","latencyMs":"0.58","fromName":"go-peer-0"}
{"peer":"go-peer-1","impl":"go","event":"receive","latencyMs":"0.80","fromName":"go-peer-0"}
{"peer":"py-peer-2","impl":"py","event":"receive","latencyMs":"2.91","fromName":"go-peer-0"}
{"peer":"py-peer-1","impl":"py","event":"receive","latencyMs":"3.01","fromName":"go-peer-0"}
{"peer":"py-peer-0","impl":"py","event":"receive","latencyMs":"3.28","fromName":"go-peer-0"}
```

**Issues, diagnosis, fixes.** "Confirmed" means reproduced or backed by logs, metrics or the library source.

| # | Symptom | Diagnosis | Status | Fix |
|---|---|---|---|---|
| 1 | Python peers dropped messages at 10 msg/s | py-libp2p defaults: a 120 s heartbeat and a 10 msg/s per-topic rate limit, which a 5-peer fan-in exceeds | Confirmed: with the default limit, the py-only check logged `Rate limit exceeded for peer … on topic lab/v1` 92 times (`results/validation/py-only-default-ratelimit/`) | 1 s heartbeat; limit raised to 500 msg/s |
| 2 | Handshake failures and connection churn when both sides dialled at once | Simultaneous dial between py-libp2p hosts | Observed in the py-only validation run; logs not kept | Only the lexicographically smaller name dials each pair |
| 3 | After a pod restart, Python peers never got back to 5 connections. Error: `handshake failed when upgrading to secure connection` | py-libp2p kept the peer's old IP in its peerstore. That IP now belonged to another libp2p host with a different identity | Confirmed: reproduced in the cluster by seeding the peerstore with another pod's IP | Re-resolve DNS and clear stored addresses before every dial; clear the 60 s negative dial cache (D12) |
| 4 | Python pods took the full 10 s grace period to stop | The Python process runs as PID 1 with no SIGTERM handler, so SIGTERM was ignored until SIGKILL. That made the restart window under the policy longer (20 s in run `full`, 9 s now) | Confirmed: the container now stops in 0.9 s | trio signal receiver cancels the nursery and logs `stopping` |
| 5 | `fromName` meant the author in Python but the forwarder in Go | py-libp2p's subscription yields only the message, whose `from_id` is the original author | Confirmed in the library source | A pass-through topic validator records the forwarder, because validators receive it |
| 6 | Messages missed during the cut came back in a ~160 s block, sent only by Python peers | Adaptive heartbeat (Section 4.3) | Mechanism confirmed in the source; interval inferred | Not changed. Documented as a finding |
| 7 | Vector's `kubernetes_logs` source shipped nothing | Not diagnosed | Confirmed by querying VictoriaLogs | Replaced by a `file` source on `/var/log/pods` (D7) |

**What I would try next** for the integration:

1. Rerun scenario 2 with `adaptive_gossip_enabled=False` to confirm item 6.
2. Run a Python publisher to measure Python-to-Go propagation directly.
3. Profile the Python receive path to explain the 4 ms median.

## 7. Limitations

- **Small sample.** Two baselines (one per network) and three isolation runs. There are no confidence intervals, and
  the late/never split varies between runs (1603/1076 versus 1733/926).
- **The isolated peers are restarted.** The restart window (9 s in `report-isolation`) is reported as its own phase.
  Messages published in it are mostly lost, and the dashboard counts two restarts per run. The old pods' logs are
  saved about 1 s before the pods are deleted, so receipts in that last second are missing. The restart-phase
  delivery numbers are therefore a lower bound.
- **One node and one clock.** Latency doesn't account for real network distance or cross-node clock skew.
- **Python peer settings are non-default** (items 1–3 in Section 6), and py-libp2p changes its own heartbeat under
  stress (item 6). Both are part of the result.
- **A clock correction broke Prometheus and Grafana for one run.** The Docker VM clock had run about 40 minutes
  ahead and was then corrected; kubectl showed `<invalid>` pod ages. As a result:
  - Prometheus held samples from the old pods stamped up to 12:25:05, and returned them as live series until about
    12:30.
  - Grafana's alert scheduler showed its last evaluation at 12:25:00 and did not evaluate the rule until then.
  - So in `report-isolation` the delivery, log and probe evidence is valid, and so are the per-pod series in
    `prometheus.csv` for the current pods. The ghost series have their own `instance` IPs, and the connectivity
    numbers in Section 4.2 use only the current pods' IPs. Its alert samples, and any aggregate across pods (counts,
    sums by pod name), are not usable.
  - `report-isolation-2` ran after 12:31 for clean metrics and alert evidence.
- **Readiness** (`/healthz`) becomes ready once the peer is subscribed, and never goes unready if the peer loses its
  connections.
- **Deduplication memory grows without limit.** The set of seen `(run, publisher, seq)` keys is never trimmed: about
  36,000 keys per hour at 10 msg/s. That is fine for runs of minutes, but not for soak tests.

## 8. Reproduce

```bash
make up                                            # cluster, images, both networks, observability, port-forwards
make test-scenario1 RUN=baseline-mixed             # mixed baseline, about 7 min
make test-scenario1 RUN=baseline-go NS=gossip-lab  # six-Go baseline
make test-scenario2 RUN=isolation                  # isolation and recovery, about 9 min, with analysis
cat results/isolation/delivery_summary.txt results/isolation/verify_during.txt
make unforward && make delete-peers                # clean up (make down removes the cluster)
```

Grafana is at http://localhost:3100 (dashboard "GossipSub lab"). VictoriaLogs is at http://localhost:9428/select/vmui.

## 9. Decisions referenced in the code

| ID | Decision |
|---|---|
| D1 | Messages missed during isolation are measured and reported, not treated as a test failure |
| D2 | Baseline thresholds set before the first run: completeness ≥ 99.9%, p99 ≤ 250 ms |
| D3 | Completeness keeps a fixed denominator during isolation, so the loss stays visible |
| D4 | One required alert (connectivity); missing telemetry shown on the dashboard |
| D5 | Deletes limited to labelled lab objects in the lab namespaces; a labelled deny policy confirmed that Calico enforces NetworkPolicy |
| D6 | Every command passes `--context kind-gossip-lab` |
| D7 | Vector reads `/var/log/pods` with a `file` source |
| D8 | Isolate one Go and one Python peer (`go-peer-2`, `py-peer-2`), never the publisher; verify the cut with TCP probes, connection counts and telemetry, not the policy object |
| D9 | A labelled busybox probe pod for reachability checks |
| D10 | Recovery expectations recorded as hypotheses before the runs |
| D11 | Restart the isolated pods under the policy so existing connections are dropped |
| D12 | Python redial re-resolves DNS and clears stored addresses every attempt |
| D13 | Alert reducer `last`, not `min` over the window |
| D14 | Images pre-loaded from the host into the kind node |
| D15 | Port-forwards on fixed ports (Grafana 3100, because 3000 is taken on the author's machine) |

## 10. Time spent and unfinished work

Time spent: TIME_SPENT

Unfinished, in priority order:

1. Confirm the adaptive-heartbeat explanation (Section 4.3) with `adaptive_gossip_enabled=False`.
2. Repeat each scenario at least five times and add automatic pass/fail assertions.
3. Isolate without restarting the pods (flush conntrack entries instead), so each process's logs cover the whole run.
4. Add delivery and missing-telemetry alerts, and a readiness check that reflects connectivity.

More ideas are listed in [`FUTURE-ENHANCEMENTS.md`](FUTURE-ENHANCEMENTS.md).
