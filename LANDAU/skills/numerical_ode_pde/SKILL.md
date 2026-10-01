---
name: "numerical_ode_pde"
description: "Use when solving ordinary or partial differential equations numerically, including choosing integrators, discretization schemes, and stability analysis."
---

# Numerical ODE/PDE Solving

Apply this skill when equations of motion, field equations, or other differential equations must be solved numerically, and you need to choose appropriate algorithms, set up discretization, and control numerical errors.

## Goal

Solve differential equations numerically with controlled accuracy, choosing appropriate methods for the problem type (stiff/non-stiff, initial/boundary value, elliptic/parabolic/hyperbolic).

## Scope

- Initial value problems (IVPs) for ODEs: Euler, Runge-Kutta (RK4, RK45), symplectic integrators, adaptive step-size methods
- Boundary value problems (BVPs): shooting method, finite difference, relaxation
- Parabolic PDEs (diffusion/heat): explicit and implicit finite difference, Crank-Nicolson
- Hyperbolic PDEs (wave): finite difference, method of lines, CFL condition
- Elliptic PDEs (Laplace/Poisson): finite difference, spectral methods, iterative solvers
- Stiff systems: implicit methods (backward Euler, BDF), LSODA
- Symplectic integrators for Hamiltonian systems (leapfrog/Verlet, higher-order symplectic)

## Inputs

- `equations`: The differential equations in explicit or implicit form
- `domain`: Spatial and/or temporal domain with boundary/initial conditions
- `parameters`: Physical constants, coupling strengths, etc.
- `accuracy_target`: Desired relative/absolute tolerance or grid resolution

## Outputs

- `method_choice`: The recommended numerical method with justification
- `discretization`: Grid/step-size setup, including adaptive criteria if applicable
- `solution_code`: Python code implementing the numerical solution (using scipy, numpy, or manual implementation)
- `stability_check`: CFL condition, stiffness assessment, or energy drift analysis

## Workflow

1. Classify the problem.
   - ODE vs PDE; initial value vs boundary value.
   - Stiff or non-stiff (check eigenvalue spread of the Jacobian).
   - For Hamiltonian systems, prefer symplectic integrators to preserve phase-space structure.

2. Choose the numerical method.
   - Non-stiff ODE IVP: RK45 (scipy.integrate.solve_ivp with method='RK45').
   - Stiff ODE IVP: BDF or Radau (scipy.integrate.solve_ivp with method='BDF').
   - Hamiltonian ODE: leapfrog/Stormer-Verlet or higher-order symplectic.
   - Parabolic PDE: Crank-Nicolson for unconditional stability.
   - Hyperbolic PDE: upwind or Lax-Wendroff with CFL condition.
   - Elliptic PDE: iterative solver (Gauss-Seidel, SOR) or direct sparse solve.

3. Set up the discretization.
   - Choose step size or grid spacing based on accuracy requirements and stability constraints.
   - For adaptive methods, set absolute and relative tolerances.

4. Implement and solve.
   - Write clean Python code using scipy.integrate or manual finite-difference stencils.
   - Include proper initial/boundary condition setup.

5. Validate.
   - Check conservation laws (energy, momentum) if applicable.
   - Perform convergence test: refine the grid/step and verify the solution changes are within tolerance.
   - For symplectic integrators, monitor energy drift over long integration times.

## Quality Checks

- Adaptive integrators should report that tolerances are met.
- CFL condition must be satisfied for explicit hyperbolic PDE solvers: c * dt / dx <= 1.
- Energy drift in Hamiltonian systems should be bounded (oscillatory, not secular) for symplectic methods.
- Grid convergence: halving the step size should reduce the error by a factor consistent with the method's order.

## Constraints

- Do not use explicit methods for stiff systems without verifying stability; prefer implicit methods.
- Do not use non-symplectic integrators for long-time Hamiltonian evolution without monitoring energy conservation.
- Always state the order of accuracy of the chosen method.
- For PDEs, always verify that boundary conditions are correctly implemented (Dirichlet, Neumann, periodic).
