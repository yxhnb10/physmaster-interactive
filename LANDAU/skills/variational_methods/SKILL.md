---
name: "variational_methods"
description: "Use when applying variational principles such as Lagrangian/Hamiltonian mechanics, the Rayleigh-Ritz method, or variational estimation of ground-state energies."
---

# Variational Methods

Apply this skill when the problem can be formulated as an extremization of a functional (action, energy, entropy, etc.), or when an approximate solution is sought by optimizing a trial ansatz.

## Goal

Formulate the problem variationally, derive the governing equations (Euler-Lagrange, Hamilton's equations), or obtain upper/lower bounds on physical quantities via trial functions.

## Scope

- Lagrangian and Hamiltonian classical mechanics
- Euler-Lagrange equations for fields and particles
- Hamilton's principle (least action)
- Rayleigh-Ritz method for eigenvalue problems
- Variational estimation of quantum ground-state energy
- Calculus of variations with constraints (Lagrange multipliers)

## Inputs

- `system_description`: The physical system (particles, fields, continua)
- `lagrangian_or_functional`: The Lagrangian, action, energy functional, or other quantity to extremize
- `constraints`: Any holonomic or non-holonomic constraints
- `trial_function`: (For Rayleigh-Ritz) A parameterized trial ansatz

## Outputs

- `equations_of_motion`: Euler-Lagrange or Hamilton's equations derived from the variational principle
- `optimized_parameters`: (For Rayleigh-Ritz) Optimal values of trial parameters
- `bound_estimate`: Upper bound on ground-state energy or extremal value of the functional
- `conserved_quantities`: Quantities conserved by virtue of symmetries (via Noether's theorem)

## Workflow

1. Identify the appropriate functional to extremize.
   - For mechanics: the action S = integral of L dt.
   - For quantum ground states: the energy functional E[psi] = <psi|H|psi> / <psi|psi>.

2. Choose generalized coordinates and identify constraints.
   - Incorporate constraints via Lagrange multipliers or by reducing degrees of freedom.

3. Derive the Euler-Lagrange equations.
   - delta S / delta q_i = 0 gives: d/dt (partial L / partial q_dot_i) - partial L / partial q_i = 0.
   - For field theories: partial_mu (partial L / partial (partial_mu phi)) - partial L / partial phi = 0.

4. (Rayleigh-Ritz) Substitute the trial function into the functional.
   - Minimize with respect to all variational parameters.
   - The result provides an upper bound on the true ground-state energy.

5. Identify conserved quantities.
   - For each continuous symmetry, apply Noether's theorem to derive the conserved current/charge.

6. Solve the resulting equations analytically or numerically.

## Quality Checks

- The Euler-Lagrange equations must be consistent with known results (e.g., Newton's second law for simple mechanical systems).
- Rayleigh-Ritz always yields an upper bound; the variational energy must be >= the true ground-state energy.
- The number of Euler-Lagrange equations must equal the number of generalized coordinates.
- Conserved quantities should be verified by showing their time derivative vanishes on-shell.

## Constraints

- The Rayleigh-Ritz method gives only an upper bound; do not claim it gives the exact energy unless the trial space contains the exact solution.
- For dissipative systems, the standard Lagrangian formulation may not apply; use Rayleigh dissipation functions or other extensions if needed.
- Non-holonomic constraints cannot always be incorporated via Lagrange multipliers in the action; use d'Alembert's principle or other appropriate methods.
