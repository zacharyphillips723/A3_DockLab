# Equation Index

The canonical equations are documented in `A3_DockLab_Project_Plan.md`.

Implementation order:

1. Circular mean motion.
2. CW differential equations and state transition matrix.
3. CW targeting boundary-value solution.
4. Nonlinear inertial two-body propagation.
5. LVLH frame transformation.
6. Quaternion kinematics and Euler rigid-body dynamics.
7. Translation and attitude control.
8. Thruster allocation and propellant flow.
9. Docking contact and combined inertia.
10. EKF and innovation consistency.
11. Safety geometry and predicted miss distance.
12. Anomaly scores and event metrics.

Every equation implemented in code should have:

- A frame definition.
- SI units.
- A source or derivation note.
- A unit test.
- A numerical conditioning note where relevant.
