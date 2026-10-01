---
name: "classical_electrodynamics"
description: "Use when solving problems involving Maxwell's equations, electrostatics, magnetostatics, electromagnetic waves, radiation, or relativistic electrodynamics."
---

# Classical Electrodynamics

Apply this skill when the problem involves electric and magnetic fields, charges and currents, electromagnetic wave propagation, radiation from accelerating charges, or relativistic formulations of electrodynamics.

## Goal

Solve electromagnetic problems using Maxwell's equations, boundary conditions, and standard techniques (method of images, multipole expansion, Green's functions, retarded potentials).

## Scope

- Electrostatics: Coulomb's law, Gauss's law, Poisson/Laplace equations, boundary value problems, method of images, multipole expansion
- Magnetostatics: Biot-Savart law, Ampere's law, magnetic vector potential, magnetic multipoles
- Electromagnetic waves: plane waves, polarization, reflection/refraction, waveguides, cavities
- Radiation: Larmor formula, retarded potentials, Lienard-Wiechert fields, dipole radiation, synchrotron radiation
- Relativistic formulation: field tensor F^{mu nu}, covariant Maxwell equations, Lorentz transformations of fields
- Electromagnetic energy and momentum: Poynting vector, Maxwell stress tensor, radiation pressure

## Inputs

- `charge_current_distribution`: Source charges and/or currents (static or time-dependent)
- `geometry`: Boundary conditions, conductor shapes, dielectric interfaces
- `medium_properties`: Permittivity, permeability, conductivity (if not vacuum)

## Outputs

- `fields`: Electric and magnetic field solutions (E, B or potentials phi, A)
- `energy_momentum`: Energy density, Poynting vector, radiated power
- `multipole_moments`: Electric and magnetic multipole moments (if applicable)

## Workflow

1. Identify the problem type.
   - Static vs time-dependent.
   - Free space vs boundaries/media.
   - Source-driven vs eigenmode problem.

2. Choose the appropriate method.
   - High symmetry (spherical, cylindrical, planar): use symmetry-adapted coordinates and separation of variables.
   - Conductors: method of images when applicable.
   - Arbitrary charge distributions: multipole expansion for far-field behavior.
   - Time-dependent sources: retarded Green's function / Lienard-Wiechert potentials.
   - Waveguides/cavities: eigenmode decomposition (TE, TM, TEM modes).

3. Solve the equations.
   - Apply boundary conditions: tangential E continuous, normal D discontinuous by sigma_free, etc.
   - For radiation problems, compute fields in the radiation zone (far field) where 1/r terms dominate.

4. Compute derived quantities.
   - Poynting vector: S = (1/mu_0) E x B.
   - Radiated power: P = integral S . dA over a closed surface.
   - Larmor formula for non-relativistic radiation: P = q^2 a^2 / (6 pi epsilon_0 c^3).

5. Verify.
   - Check Maxwell's equations are satisfied.
   - Verify boundary conditions.
   - Check limiting cases (far field, near field, static limit).

## Quality Checks

- Divergence of B must be zero everywhere.
- In source-free regions, divergence of E must be zero (or equal to rho/epsilon_0 with sources).
- Energy conservation: rate of change of field energy + Poynting flux = - J . E (work done on charges).
- Far-field radiation must fall off as 1/r, with power going as 1/r^2.

## Constraints

- Always specify the gauge choice when using potentials (Coulomb gauge, Lorenz gauge, etc.).
- Do not mix SI and Gaussian units; state the unit system explicitly.
- Method of images applies only when the geometry allows exact image placement; do not use it for arbitrary shapes.
- Retarded potentials, not instantaneous ones, must be used for time-dependent sources.
