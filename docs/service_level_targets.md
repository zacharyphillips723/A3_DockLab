# Service-level targets

These targets define the expected development and demonstration envelope. They
are not a crew-rated or safety-critical availability claim.

| Signal | Target | Measurement |
|---|---:|---|
| Interactive API latency | p95 ≤ 250 ms, excluding model inference | `/api/operations/metrics` |
| Built-in policy latency | p95 ≤ configured budget (default 50 ms) | policy evaluations |
| Live display delivery | 10 Hz target; < 1% dropped browser frames | client metrics endpoint |
| Session reconnect | ≤ 60 s after lease expiry | recovery acceptance test |
| Lakebase health query | p95 ≤ 250 ms | operational metrics storage block |
| Completed-run publication | ≤ 5 min under normal Job capacity | job completion and run manifest |
| Concurrent demonstration load | 8 active sessions tested; configured ceiling 32 | load acceptance test |

Alert when two consecutive five-minute windows exceed a latency target, when
Lakebase health is false, when dropped frames exceed 1%, or when any accepted
command cannot be recovered. The JSON metrics endpoint intentionally uses only
low-cardinality route templates and aggregate session counts.

