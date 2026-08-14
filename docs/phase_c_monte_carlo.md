# Phase C: Monte Carlo and Risk Summaries

## Sampling contract

Monte Carlo configuration declares an ordered set of bounded normal parameter
distributions and a correlation matrix. The matrix must be symmetric, have a
unit diagonal, and be positive semidefinite. Sampling uses NumPy `SeedSequence`
child streams so each sample has a stable independent seed and the complete
ensemble is reproducible from its root seed.

Initial-state dispersions in the default file are baseline-relative offsets, so
one ensemble configuration can apply to either reference mission. Mass and
thrust dispersions are multiplicative scale factors.

Each declared fault has a Bernoulli occurrence probability, severity, and
normalized start/duration window. Fault draws use the same deterministic child
stream as their sample, so rerunning an ensemble preserves both physical
dispersions and the fault catalog selected for every member. Telemetry faults
are applied only while generating sensor/navigation streams; handoff faults are
passed to the authority-transfer state machine. Fault labels never enter the
estimator.

## Local runner

Run an ensemble with:

```bash
a3docklab monte-carlo configs/scenarios/blue_moon_side.yaml \
  --ensemble-config configs/monte_carlo/default.yaml \
  --telemetry-config configs/telemetry/default.yaml \
  --output ensembles
```

The output contains:

- `manifest.json`: stable ensemble identity, seed, sample count, parameter
  ordering, configuration hash, execution backend, partition count, and seed
  strategy.
- `runs.parquet`: sampled parameters, selected faults, mission outcomes, and
  per-run EKF RMSE, NIS consistency, and covariance-growth diagnostics.
- `convergence.parquet`: running capture/abort rates, capture-rate standard
  error, and mean propellant.
- `risk_summary.json`: capture, abort, rollback, fuel, closest-approach,
  alignment, angular-error, fault prevalence, and estimator tail summaries.

The machine-readable contract is `docs/contracts/phase_c_ensemble.yaml`. A
Databricks runner should preserve this contract and seed semantics while
parallelizing sample execution; distributed execution must not redefine the
physics or aggregate definitions.

## Interpretation limits

Convergence of an ensemble only establishes stability under the declared input
distributions. It does not validate whether those distributions represent the
real vehicles. Clipped bounded normals also accumulate probability at their
bounds, so saturation counts should be reviewed when interpreting tails.

## Next increment

Implement distributed Databricks Job/MLflow orchestration and Lakebase-backed
mutable application state on top of the completed Delta adapter boundary.
