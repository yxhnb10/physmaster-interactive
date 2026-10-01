---
name: "thermodynamics_statistical_mechanics"
description: "Use when computing partition functions, thermodynamic potentials, phase transitions, equations of state, or ensemble averages."
---

# Thermodynamics and Statistical Mechanics

Apply this skill when the problem involves thermal equilibrium, partition functions, free energies, entropy, equations of state, phase transitions, or the statistical behavior of many-particle systems.

## Goal

Compute thermodynamic quantities from microscopic models using ensemble theory, or apply thermodynamic identities and potentials to macroscopic systems.

## Scope

- Microcanonical, canonical, and grand canonical ensembles
- Partition functions and free energies (Helmholtz F, Gibbs G, grand potential Omega)
- Equations of state and thermodynamic response functions (heat capacity, compressibility, susceptibility)
- Classical ideal gas, quantum ideal gases (Bose-Einstein, Fermi-Dirac)
- Phase transitions: Ehrenfest classification, Landau theory, critical exponents, mean-field theory
- Fluctuations and fluctuation-dissipation theorem
- Entropy: Boltzmann, Gibbs, Shannon, von Neumann
- Monte Carlo methods: Metropolis algorithm, importance sampling

## Inputs

- `microscopic_model`: Hamiltonian or energy function of the system
- `ensemble`: Which ensemble to use (microcanonical, canonical, grand canonical)
- `control_parameters`: Temperature T, pressure P, chemical potential mu, external fields
- `particle_statistics`: Classical, bosonic, or fermionic

## Outputs

- `partition_function`: Z (or its logarithm) in the appropriate ensemble
- `thermodynamic_potentials`: F, G, Omega, S, U as functions of control parameters
- `equations_of_state`: Pressure, density, magnetization as functions of T, V, N, etc.
- `phase_diagram`: Location of phase boundaries and critical points (if applicable)

## Workflow

1. Identify the ensemble.
   - Fixed E, V, N -> microcanonical (Omega = k_B ln W).
   - Fixed T, V, N -> canonical (Z = sum exp(-beta E_i), F = -k_B T ln Z).
   - Fixed T, V, mu -> grand canonical (Xi = sum exp(-beta(E_i - mu N_i))).

2. Compute the partition function.
   - Sum (or integrate) over all microstates.
   - For quantum systems, include the correct statistics (Bose-Einstein or Fermi-Dirac).
   - Use saddle-point / steepest descent for large N when exact evaluation is intractable.

3. Derive thermodynamic quantities.
   - Internal energy: U = -partial(ln Z)/partial(beta).
   - Entropy: S = -partial F / partial T.
   - Pressure: P = -partial F / partial V.
   - Heat capacity: C_V = partial U / partial T at constant V.
   - Use Maxwell relations to connect different response functions.

4. Analyze phase transitions (if applicable).
   - Look for non-analyticities in the free energy as a function of control parameters.
   - Landau theory: expand free energy in an order parameter near the critical point.
   - Compute critical exponents (mean-field or beyond).

5. Numerical methods (if needed).
   - Monte Carlo simulation with Metropolis algorithm for interacting systems.
   - Molecular dynamics for time-dependent thermodynamic properties.

## Quality Checks

- The free energy must be extensive (proportional to system size) in the thermodynamic limit.
- Entropy must be non-negative and satisfy the third law (S -> 0 as T -> 0 for non-degenerate ground states).
- Heat capacity must be non-negative: C_V >= 0.
- Thermodynamic identities (Maxwell relations) must be self-consistent.
- In the high-temperature limit, quantum results should reduce to classical results.

## Constraints

- Do not use the canonical ensemble for systems that exchange particles with a reservoir; use the grand canonical ensemble.
- Mean-field theory gives incorrect critical exponents near phase transitions in low dimensions (d <= 4 for Ising-like systems); state this limitation explicitly.
- For quantum gases, do not neglect quantum statistics (Bose-Einstein condensation, Fermi surface) at temperatures comparable to the degeneracy temperature.
- Always state whether the calculation is in the thermodynamic limit (N -> infinity, V -> infinity, N/V fixed) or for a finite system.
