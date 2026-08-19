# Milestone 3 — Live Lab implementation

Milestone 3 changes A3 DockLab's primary workflow from passive replay to a
human-driven deterministic simulation session. Historical Replay remains a
separate application tab.

## Runtime design

- `InteractiveSimulationService` owns in-process sessions behind a thread-safe
  registry. Each session has one opaque control lease; status and reconnect
  reads are public, while every mutation requires the lease token.
- The Flask API creates sessions, reports the latest checkpoint, accepts
  lifecycle and control actions, and exports the authoritative command log.
- The browser owns the display clock. It sends guarded intent to the live API
  and applies returned frames with Plotly `extendTraces` and `restyle`; no Dash
  callback rebuilds a live figure.
- Simulation checkpoints retain the complete intent sequence. The exported
  command log plus scenario and deterministic fault selection are sufficient to
  reproduce the human-driven run through `SimulationSession`.
- Non-finite internal telemetry is represented as JSON `null` at the HTTP
  boundary and is not changed inside the simulation.

## Live controls

- Create, run, pause, single-step, reset, and terminate
- 2, 5, 10, and 20 Hz browser display rates
- Autopilot, velocity, hold, retreat, capture, and abort intent
- Translational velocity and attitude-torque vectors
- Deterministic handoff fault selection at session creation
- Live 3D trajectory/head marker, range/closing-rate history, KPIs, events, and
  requested-versus-executed safety decisions

## API

- `GET /api/simulations/scenarios`
- `POST /api/simulations`
- `GET /api/simulations/{session_id}`
- `GET /api/simulations/{session_id}/commands`
- `POST /api/simulations/{session_id}/control`

The local registry is intentionally replaceable. Milestone 5 will persist
ownership, leases, commands, and checkpoints in Lakebase/Delta without changing
the simulation or browser command contracts.
