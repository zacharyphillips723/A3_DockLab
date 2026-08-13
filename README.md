# A3 DockLab

A3 DockLab is an open-source Artemis III rendezvous-and-docking digital twin. It models Orion as the chaser spacecraft for two low-Earth-orbit demonstrations:

1. Side docking with a Blue Moon Mark 2-derived test lander, followed by Orion-controlled docked-stack operations.
2. Nose-to-nose docking with a Starship Version 3-derived test article, followed by Starship-controlled docked-stack operations.

The repository is intentionally an engineering research simulator, not flight software and not a representation of proprietary NASA, Blue Origin, SpaceX, Lockheed Martin, or ESA data.

## What is included

- Clohessy-Wiltshire relative-motion propagation
- Nonlinear two-body propagation scaffold
- Configurable vehicle, docking, sensor, thruster, and safety assumptions
- Phase-based rendezvous state machine
- Synthetic telemetry and fault injection schemas
- Anomaly-detection experiment plan
- Dashboard and 3D visualization architecture
- Verification and Monte Carlo strategy

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e ".[dev]"
pytest
python -m a3docklab.cli configs/scenarios/blue_moon_side.yaml --output runs
```

Install optional components as needed:

```bash
pip install -e ".[ui,ml,storage,astro]"
```

Each run is written as a portable bundle containing `telemetry.csv` and
`metadata.json`. The metadata records a deterministic run ID, configuration
hash, random seed, mission-source revision, and the complete assumption
manifest used by the run.

## Databricks integration boundary

Simulation code writes through the `RunStorage` protocol. The included
`LocalRunStorage` keeps local development simple; a later Databricks adapter can
write the same telemetry and metadata contracts to Delta tables while MLflow
tracks experiments. Lakebase should hold application-facing state such as run
annotations and saved views, rather than becoming a dependency of the physics
engine.

## Project document

See `docs/A3_DockLab_Project_Plan.md` for the full system design, equations, library choices, testing plan, implementation roadmap, and known risks.

## Status

Starter architecture and reference implementation. All vehicle values are public-source estimates or explicit engineering placeholders. Replace them through configuration as better public information becomes available.

## License

MIT. NASA, Artemis, Orion, Blue Moon, Blue Origin, Starship, and SpaceX names remain the property of their respective owners. This project is unaffiliated with and not endorsed by those organizations.
