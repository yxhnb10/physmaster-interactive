---
name: "dimensional_analysis"
description: "Use when checking dimensional consistency, estimating physical scales, or deriving functional forms via the Buckingham Pi theorem."
---

# Dimensional Analysis

Apply this skill when you need to verify that equations are dimensionally consistent, estimate the order of magnitude of a physical quantity, or derive the functional dependence of a quantity on relevant parameters without solving the full equations.

## Goal

Use dimensional reasoning to constrain or derive physical relationships, check equation correctness, and estimate scales.

## Scope

- SI and natural unit systems (h-bar = c = 1, Gaussian, Heaviside-Lorentz, lattice units, etc.)
- Buckingham Pi theorem for systematic reduction
- Order-of-magnitude estimation

## Inputs

- `physical_quantities`: The relevant dimensional quantities (masses, lengths, times, charges, etc.)
- `target_quantity`: The quantity whose dimensions or functional form you want to determine
- `unit_system`: The unit convention in use (SI, natural, CGS, lattice, etc.)

## Outputs

- `dimensional_check`: Whether each equation or expression is dimensionally consistent (pass/fail with explanation)
- `pi_groups`: Dimensionless combinations identified via Buckingham Pi (if applicable)
- `estimated_scale`: Order-of-magnitude estimate of the target quantity

## Workflow

1. List all independent dimensional quantities and their dimensions in the chosen unit system.
2. Identify the independent base dimensions (e.g., M, L, T, Q).
3. If checking an equation: verify that every term shares the same dimensions.
4. If deriving a relation: apply the Buckingham Pi theorem.
   - Count the number of quantities (n) and independent base dimensions (k).
   - Form (n - k) independent dimensionless Pi groups.
   - Express the target quantity as a function of dimensionless groups times a dimensional prefactor.
5. If estimating a scale: substitute typical numerical values to obtain an order-of-magnitude result.

## Quality Checks

- Every term in a valid equation must have identical dimensions.
- The number of independent Pi groups must equal n - k.
- Natural-unit expressions must be convertible back to SI with appropriate powers of h-bar and c.

## Constraints

- Dimensional analysis determines functional form up to dimensionless numerical coefficients; do not claim exact prefactors unless derived from a full calculation.
- When using natural units, always state the conversion factors explicitly if the user needs SI results.
- Do not silently mix unit systems within the same expression.
