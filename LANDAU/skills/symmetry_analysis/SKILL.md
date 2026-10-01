---
name: "symmetry_analysis"
description: "Use when identifying symmetries of a physical system, applying group theory, classifying representations, or deriving selection rules."
---

# Symmetry Analysis

Apply this skill when the problem benefits from identifying and exploiting symmetries — to simplify equations, classify states, derive selection rules, or understand degeneracies and conservation laws.

## Goal

Identify the symmetry group of the system, classify physical quantities under its representations, and use symmetry to constrain or simplify the solution.

## Scope

- Discrete symmetries: parity (P), time reversal (T), charge conjugation (C), lattice point groups
- Continuous symmetries: rotation SO(3)/SU(2), translation, Lorentz/Poincare, internal gauge symmetries
- Noether's theorem: continuous symmetry -> conserved current
- Representation theory: irreducible representations, tensor products, Clebsch-Gordan decomposition
- Selection rules: matrix element vanishing by symmetry
- Spontaneous symmetry breaking and Goldstone's theorem

## Inputs

- `system_description`: Hamiltonian, Lagrangian, or equation of motion
- `symmetry_group`: Known or suspected symmetry group (may need to be identified)
- `quantities_of_interest`: Operators, states, or matrix elements to classify

## Outputs

- `symmetry_group_identified`: The symmetry group and its generators
- `representation_classification`: How states/operators transform under the group
- `selection_rules`: Which matrix elements vanish by symmetry
- `conserved_quantities`: Conserved charges/currents from continuous symmetries
- `degeneracy_structure`: Multiplet structure and degeneracy pattern

## Workflow

1. Identify the symmetry group.
   - Examine the Hamiltonian/Lagrangian for invariance under transformations.
   - List all generators and verify they close under commutation (Lie algebra).

2. Classify states by irreducible representations.
   - For finite groups: use character tables.
   - For Lie groups: use weight diagrams, Casimir operators, or highest-weight construction.

3. Derive selection rules.
   - A matrix element <f|O|i> vanishes unless the tensor product of representations of |i>, O, and <f| contains the trivial representation.
   - For angular momentum: apply Wigner-Eckart theorem and triangle rule.

4. Identify conserved quantities.
   - For each continuous symmetry generator G: [H, G] = 0 implies G is conserved.
   - Write the explicit conserved current via Noether's theorem if working with a Lagrangian.

5. Analyze symmetry breaking (if applicable).
   - Determine if the ground state breaks a symmetry of the Hamiltonian.
   - Count Goldstone bosons: one for each broken continuous generator (for relativistic systems).

## Quality Checks

- The identified generators must satisfy the correct commutation relations of the group.
- Selection rules must be consistent with known experimental results or exact solutions.
- The number of Goldstone bosons must match the number of broken generators (in relativistic theories).
- Conserved quantities should have vanishing Poisson bracket (classical) or commutator (quantum) with the Hamiltonian.

## Constraints

- Do not assume a symmetry without verifying invariance of the full Hamiltonian (including interactions).
- Selection rules are necessary conditions for non-vanishing matrix elements, not sufficient; a matrix element allowed by symmetry may still be numerically small.
- Spontaneous symmetry breaking in finite systems (e.g., finite quantum systems) requires careful treatment; true SSB occurs only in the thermodynamic/infinite-volume limit.
