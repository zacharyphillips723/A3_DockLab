# Verification Plan

## Required automated suites

- Unit tests for equations and transforms.
- Property-based tests for invariants.
- Integration tests for complete mission phases.
- Regression tests for terminal states, fuel, and alert timing.
- Statistical tests for Monte Carlo and detector calibration.

## Required independent comparisons

- Analytic CW versus numerical CW integration.
- CW versus nonlinear truth by range and propagation duration.
- Combined inertia versus independent parallel-axis calculation.
- Stored telemetry versus dashboard values.
- Injected fault timeline versus detected event timeline.

## Release gate

A release is blocked by:

- Unclassified assumptions.
- Unit or frame ambiguity.
- Failed deterministic safety tests.
- Data leakage in ML experiments.
- Missing event-level false-alarm or detection-delay metrics.
