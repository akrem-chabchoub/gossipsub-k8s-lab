"""Tests for scripts/analyze_phase.py, which produces every delivery number in REPORT.md.

Run from the repository root: python3 -m unittest discover -s scripts/tests
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "analyze_phase.py")
RUN = "20260101T000000Z"
T0 = 1_767_225_600 * 10**9          # 2026-01-01T00:00:00Z in ns
FROM, TO = "2026-01-01T00:00:00Z", "2026-01-01T00:00:10Z"
S = 10**9


def pub(seq, at_ns, peer="go-peer-0", run=RUN):
    return {"event": "publish", "peer": peer, "runId": run, "seq": seq, "publishedAtNs": at_ns}


def rcv(peer, seq, published_ns, received_ns, run=RUN):
    return {"event": "receive", "peer": peer, "runId": run, "seq": seq, "publishedAtNs": published_ns,
            "receivedAtNs": received_ns, "latencyMs": (received_ns - published_ns) / 1e6}


class AnalyzePhaseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, name, events, extra_lines=()):
        path = os.path.join(self.dir.name, name)
        with open(path, "w") as f:
            for line in extra_lines:
                f.write(line + "\n")
            for e in events:
                f.write(json.dumps(e) + "\n")
        return path

    def run_script(self, logs, grace="2", run=RUN, publisher="go-peer-0"):
        return subprocess.run([sys.executable, SCRIPT, "--publisher", publisher, "--run", run, "--from", FROM,
                               "--to", TO, "--grace-s", grace, *logs], capture_output=True, text=True)

    def peer_line(self, out, peer):
        m = re.search(rf"^  {peer}: (.*)$", out, re.M)
        self.assertIsNotNone(m, f"no line for {peer} in:\n{out}")
        return m.group(1)

    def test_on_time_late_never_and_phase_bounds(self):
        # seq 1..10 at T0, T0+1s, ...; seq 0 (before from) and seq 11 (at to) are outside [from, to).
        pubs = [pub(s, T0 + (s - 1) * S) for s in range(0, 12)]
        publisher = self.write("go-peer-0.log", pubs)
        ms = 10**6
        a = self.write("a.log", [rcv("peer-a", s, T0 + (s - 1) * S, T0 + (s - 1) * S + ms) for s in range(1, 11)])
        # peer-b misses seq 9 and 10.
        b = self.write("b.log", [rcv("peer-b", s, T0 + (s - 1) * S, T0 + (s - 1) * S + ms) for s in range(1, 9)])
        # peer-c: seq 1 exactly at to + grace (on time), seq 2 one ns later (late), rest on time.
        c_events = [rcv("peer-c", 1, T0, T0 + 12 * S), rcv("peer-c", 2, T0 + S, T0 + 12 * S + 1)]
        c_events += [rcv("peer-c", s, T0 + (s - 1) * S, T0 + (s - 1) * S + ms) for s in range(3, 11)]
        c = self.write("c.log", c_events)

        r = self.run_script([publisher, a, b, c], grace="2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("published=10", r.stdout)
        self.assertIn("on_time=10/10 (100.0%) late=0 never=0", self.peer_line(r.stdout, "peer-a"))
        self.assertIn("on_time=8/10 (80.0%) late=0 never=2", self.peer_line(r.stdout, "peer-b"))
        line_c = self.peer_line(r.stdout, "peer-c")
        self.assertIn("on_time=9/10 (90.0%) late=1 never=0", line_c)
        self.assertIn("late_arrived=00:00:12.000->00:00:12.000", line_c)
        self.assertNotIn("go-peer-0:", r.stdout)  # the publisher is not a subscriber

    def test_grace_zero_counts_post_phase_receipts_as_late(self):
        publisher = self.write("go-peer-0.log", [pub(1, T0)])
        a = self.write("a.log", [rcv("peer-a", 1, T0, T0 + 10 * S + 1)])  # 1 ns after `to`
        r = self.run_script([publisher, a], grace="0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("on_time=0/1 (0.0%) late=1 never=0", self.peer_line(r.stdout, "peer-a"))

    def test_files_of_one_peer_are_merged_and_earliest_receipt_wins(self):
        publisher = self.write("go-peer-0.log", [pub(1, T0), pub(2, T0 + S)])
        before = self.write("before.log", [rcv("go-peer-2", 1, T0, T0 + 5 * 10**6)])
        # After the restart: a second, later copy of seq 1 and the first copy of seq 2.
        after = self.write("after.log", [rcv("go-peer-2", 1, T0, T0 + 9 * S), rcv("go-peer-2", 2, T0 + S, T0 + S + 10**6)])
        r = self.run_script([publisher, after, before])  # file order must not matter
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("go-peer-2:"), 1)
        line = self.peer_line(r.stdout, "go-peer-2")
        self.assertIn("on_time=2/2 (100.0%) late=0 never=0", line)
        self.assertIn("p99_ms=5.00", line)  # seq 1 keeps the earlier 5 ms receipt, not the 9 s copy

    def test_other_runs_publishers_and_garbage_are_ignored(self):
        publisher = self.write("go-peer-0.log", [pub(1, T0), pub(2, T0, run="other-run"), pub(3, T0, peer="py-peer-0")],
                               extra_lines=["not json", '{"truncated": '])
        a = self.write("a.log", [rcv("peer-a", 1, T0, T0 + 10**6), rcv("peer-a", 1, T0, T0 + 10**6, run="other-run")])
        r = self.run_script([publisher, a])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("published=1", r.stdout)
        self.assertIn("on_time=1/1", self.peer_line(r.stdout, "peer-a"))

    def test_empty_phase_fails(self):
        publisher = self.write("go-peer-0.log", [pub(1, T0)])
        for kwargs in ({"run": "missing-run"}, {"publisher": "go-peer-9"}):
            r = self.run_script([publisher], **kwargs)
            self.assertEqual(r.returncode, 1, kwargs)
            self.assertIn("no messages published", r.stderr)

    def test_empty_run_id_fails(self):
        publisher = self.write("go-peer-0.log", [pub(1, T0)])
        r = self.run_script([publisher], run="")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("empty --run", r.stderr)


if __name__ == "__main__":
    unittest.main()
