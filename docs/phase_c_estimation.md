# Phase C: Relative-Navigation Estimation

## Implemented estimator

The navigation pipeline now runs a six-state CW extended Kalman filter for LVLH
position and velocity. The filter predicts at `navigation_rate_hz` and updates
only on actual `relative_sensor_rate_hz` measurements. Prediction-only samples
retain `measurement_used=false` and null innovation/NIS values; measurements are
never silently forward-filled.

The process covariance models independent white acceleration on each axis. The
measurement covariance comes from the configured position and velocity sensor
noise. Measurement updates use Joseph form to preserve covariance symmetry and
positive semidefiniteness under finite precision.

## Diagnostics

Schema 3.0 records:

- Filtered position and velocity.
- Per-axis position and velocity standard deviations.
- Covariance trace.
- Six-component innovation.
- Normalized innovation squared (NIS).
- Configured NIS threshold and consistency result.
- Whether a measurement was used at that estimator sample.

NIS is a model-consistency diagnostic, not proof that the filter is correct. The
current process model is uncontrolled CW while the truth trajectory includes
bounded control and docked-stack transitions, so elevated NIS can reveal real
model mismatch rather than merely bad sensor noise.

## Next increment

Define reproducible parameter distributions and correlated sampling, then build
a local Monte Carlo runner with ensemble manifests, convergence diagnostics, and
docking/abort/fuel/alignment/handoff risk summaries. The same domain contracts
can later back a Databricks distributed runner.
