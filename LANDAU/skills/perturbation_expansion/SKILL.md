---
name: "perturbation_expansion"
description: "Use when solving a problem by expanding around a known solution in a small parameter, including regular and singular perturbation theory."
---

# Perturbation Expansion

Apply this skill when the problem contains a small dimensionless parameter and the solution can be constructed order-by-order as a series expansion around a known zeroth-order solution.

## Goal

Systematically expand equations and their solutions in powers of a small parameter, compute corrections order by order, and assess the validity of the expansion.

## Scope

- Regular perturbation theory (algebraic and differential equations)
- Time-independent perturbation theory in quantum mechanics (non-degenerate and degenerate)
- Time-dependent perturbation theory and Fermi's golden rule
- Rayleigh-Schrodinger and Brillouin-Wigner perturbation theory
- Singular perturbation theory and boundary layers (when the small parameter multiplies the highest derivative)
- Asymptotic series and their convergence properties

## Inputs

- `unperturbed_system`: The exactly solvable zeroth-order problem (Hamiltonian, equation, potential, etc.)
- `perturbation`: The small correction term
- `small_parameter`: The expansion parameter (epsilon, coupling constant, etc.) and its numerical value or range
- `order`: The desired order of the expansion

## Outputs

- `corrections`: The perturbative corrections at each requested order (energy shifts, wavefunctions, amplitudes, etc.)
- `expanded_solution`: The full solution up to the requested order
- `validity_estimate`: Estimate of the regime where the expansion is reliable

## Workflow

1. Identify the small parameter and verify it is dimensionless and numerically small.
   - If the parameter is not manifestly small, state the assumption and the regime of validity.

2. Write the full problem as: H = H_0 + epsilon * H_1 (or analogous).
   - Solve H_0 exactly to obtain the zeroth-order solution.

3. Expand the solution in powers of epsilon:
   - psi = psi_0 + epsilon * psi_1 + epsilon^2 * psi_2 + ...
   - E = E_0 + epsilon * E_1 + epsilon^2 * E_2 + ...

4. Substitute into the full equation and collect terms at each order of epsilon.
   - At order epsilon^n: solve for psi_n and E_n using lower-order quantities.

5. For degenerate perturbation theory:
   - Diagonalize the perturbation within the degenerate subspace before proceeding.

6. Assess convergence:
   - Compare the magnitude of successive corrections.
   - Identify if the series is asymptotic (divergent but useful at low orders).

## Quality Checks

- Each correction must have consistent dimensions with the quantity it corrects.
- The ratio |epsilon^(n+1) * correction_(n+1)| / |epsilon^n * correction_n| should decrease for the expansion to be reliable.
- For quantum mechanical perturbation theory, orthogonality of corrections to the unperturbed state must be maintained (in the standard normalization convention).
- Secular terms (terms growing unboundedly in time) indicate breakdown of naive perturbation theory; suggest resummation or multi-scale methods if they appear.

## Constraints

- Do not apply regular perturbation theory if the perturbation is singular (e.g., it changes the order of a differential equation or boundary conditions).
- Do not claim convergence of the perturbation series unless proven; most physics perturbation series are asymptotic.
- For degenerate cases, always resolve the degeneracy before computing corrections.
- State explicitly which parameter is small and the assumed ordering.
