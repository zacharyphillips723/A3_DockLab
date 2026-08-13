# Contributing

A3 DockLab welcomes contributions in astrodynamics, controls, telemetry, visualization, anomaly detection, documentation, and verification.

## Engineering rules

1. Use SI units internally.
2. State every coordinate frame at public API boundaries.
3. Tag every vehicle value as public fact, derived estimate, or project assumption.
4. Add tests for equations, frame transforms, and fault timing.
5. Never describe project values as NASA flight limits or certified vehicle parameters.
6. Preserve deterministic seeds for reproducible Monte Carlo runs.

## Pull requests

- Include a concise technical rationale.
- Add or update tests.
- Update assumptions and equations documentation when behavior changes.
- Run `ruff check .`, `mypy src`, and `pytest` before submission.
