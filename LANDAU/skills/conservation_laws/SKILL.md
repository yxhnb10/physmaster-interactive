---
name: "conservation_laws"
description: "Use when applying conservation of energy, momentum, angular momentum, charge, or other conserved quantities to constrain or solve a physical system."
---

# Conservation Laws

Apply this skill when a problem involves identifying conserved quantities, using them to reduce degrees of freedom, or constraining kinematics and dynamics via conservation principles.

## Goal

Identify applicable conservation laws, derive the conserved quantities, and use them to constrain or solve the problem.

## Scope

- Energy conservation (mechanical, thermodynamic, relativistic)
- Linear and angular momentum conservation
- Charge conservation and continuity equations
- Other conserved currents from Noether's theorem (baryon number, lepton number, etc.)
- Relativistic 4-momentum conservation in scattering and decay processes

## Inputs

- `system_description`: Description of the physical system and its interactions
- `known_quantities`: Masses, velocities, charges, fields, or other given data
- `symmetries`: Any stated or inferred symmetries (translational, rotational, gauge, etc.)

## Outputs

- `conserved_quantities`: List of applicable conserved quantities with justification
- `constraint_equations`: Explicit equations relating initial and final states
- `solution`: Derived unknowns from the conservation constraints

## Workflow

1. Identify the symmetries of the system.
   - Translational invariance -> linear momentum conservation.
   - Rotational invariance -> angular momentum conservation.
   - Time-translation invariance -> energy conservation.
   - Gauge invariance -> charge conservation.

2. Write down the conserved quantity expressions.
   - For each symmetry, write the corresponding conserved current or integral of motion.

3. Set up constraint equations.
   - Equate the conserved quantity evaluated at the initial and final states.
   - For relativistic problems, use 4-momentum conservation: sum of initial 4-momenta = sum of final 4-momenta.

4. Solve the constraint system.
   - Eliminate unknowns using the conservation equations.
   - If the system is under-determined, state which additional information is needed.

5. Verify consistency.
   - Check that the solution does not violate any conservation law.
   - For scattering problems, verify that threshold energies are satisfied.

## Quality Checks

- All conservation equations must be dimensionally consistent.
- For relativistic kinematics, verify that invariant mass is preserved: (sum p_mu)^2 is Lorentz-invariant.
- Energy must be non-negative; momenta must be real-valued for physical solutions.
- If dissipative forces are present, energy conservation must account for heat or radiation losses.

## Constraints

- Do not apply conservation of mechanical energy in systems with non-conservative forces without accounting for energy dissipation.
- Do not assume angular momentum conservation unless the net external torque is zero about the chosen axis.
- In relativistic problems, use 4-vectors consistently; do not mix relativistic and non-relativistic expressions.
