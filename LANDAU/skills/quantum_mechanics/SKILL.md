---
name: "quantum_mechanics"
description: "Use when solving quantum mechanical problems including the Schrodinger equation, angular momentum coupling, scattering theory, or many-body quantum systems."
---

# Quantum Mechanics

Apply this skill for problems involving quantum states, operators, time evolution, measurement, angular momentum algebra, scattering amplitudes, or many-body quantum systems.

## Goal

Set up and solve quantum mechanical problems using the Schrodinger equation, operator algebra, symmetry principles, and standard approximation methods.

## Scope

- Time-independent Schrodinger equation: bound states, energy spectra, wavefunctions
- Time-dependent Schrodinger equation: evolution operators, transition amplitudes
- Angular momentum: addition of angular momenta, Clebsch-Gordan coefficients, Wigner-Eckart theorem
- Identical particles: bosons, fermions, Slater determinants, second quantization
- Scattering theory: Born approximation, partial wave analysis, optical theorem, S-matrix
- Density matrices and mixed states
- WKB approximation for semiclassical problems
- Path integral formulation (when appropriate)

## Inputs

- `hamiltonian`: The Hamiltonian operator (or potential for single-particle problems)
- `initial_state`: Initial wavefunction or quantum state
- `observables`: Operators whose expectation values or spectra are sought
- `boundary_conditions`: Normalizability, periodicity, scattering boundary conditions

## Outputs

- `energy_spectrum`: Eigenvalues and their degeneracies
- `wavefunctions`: Eigenstates or time-evolved states
- `expectation_values`: <O> for requested observables
- `transition_amplitudes`: Matrix elements, scattering amplitudes, cross sections

## Workflow

1. Formulate the Hamiltonian and identify the Hilbert space.
   - Choose a suitable basis (position, momentum, energy, angular momentum).
   - Identify symmetries to simplify the problem (parity, rotational symmetry -> good quantum numbers).

2. Solve the eigenvalue problem (time-independent case).
   - For exactly solvable potentials (harmonic oscillator, hydrogen atom, infinite well): use analytic methods.
   - For general potentials: use variational methods, perturbation theory, or numerical diagonalization.
   - Apply boundary conditions: normalizability for bound states, asymptotic plane waves for scattering.

3. Time evolution (time-dependent case).
   - For time-independent H: psi(t) = exp(-iHt/h-bar) psi(0), expand in energy eigenstates.
   - For time-dependent H: use time-dependent perturbation theory or numerical propagation.

4. Compute observables.
   - <O> = <psi|O|psi> for pure states; Tr(rho O) for mixed states.
   - Uncertainties: delta O = sqrt(<O^2> - <O>^2).
   - Transition rates: Fermi's golden rule for perturbative transitions.

5. Scattering (if applicable).
   - Set up the asymptotic boundary conditions: incident plane wave + outgoing spherical wave.
   - Compute the scattering amplitude f(theta, phi).
   - Differential cross section: d sigma / d Omega = |f|^2.
   - Total cross section: sigma_tot via the optical theorem.

## Quality Checks

- Wavefunctions must be normalizable (bound states) or have proper scattering asymptotics.
- Energy eigenvalues must be real for Hermitian Hamiltonians.
- Uncertainty relations must be satisfied: delta x * delta p >= h-bar / 2.
- Unitarity: S^dag S = 1; probability is conserved.
- For angular momentum coupling, verify that total J quantum numbers satisfy the triangle inequality.

## Constraints

- Do not confuse state vectors with wavefunctions; keep the representation explicit.
- For identical particles, always impose the correct exchange symmetry (antisymmetric for fermions, symmetric for bosons).
- WKB is valid only when the potential varies slowly compared to the local de Broglie wavelength; do not apply it near classical turning points without connection formulas.
- The Born approximation is valid for weak scattering potentials; do not use it for strong potentials without justification.
