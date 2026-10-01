---
name: "statistical_error_analysis"
description: "Use when propagating uncertainties, performing error analysis, fitting data with error bars, or assessing statistical significance of physical measurements."
---

# Statistical Error Analysis

Apply this skill when physical measurements or computed quantities carry uncertainties and you need to propagate errors, fit models to data, or assess the statistical reliability of results.

## Goal

Quantify uncertainties in derived quantities, perform proper error propagation, fit models to data with chi-squared or likelihood methods, and report results with correct statistical interpretation.

## Scope

- Gaussian error propagation (linear and nonlinear)
- Systematic vs statistical uncertainties
- Weighted and unweighted least-squares fitting
- Chi-squared goodness of fit and reduced chi-squared
- Covariance matrices and correlated errors
- Bootstrap and jackknife resampling
- Confidence intervals and hypothesis testing
- Monte Carlo error propagation for complex functions

## Inputs

- `measurements`: Data values with their uncertainties (statistical and/or systematic)
- `model_function`: The theoretical model to fit or the function through which errors propagate
- `correlations`: Covariance matrix or correlation information between measurements (if any)

## Outputs

- `propagated_uncertainty`: The uncertainty on derived quantities with the propagation formula used
- `fit_parameters`: Best-fit values with their uncertainties
- `goodness_of_fit`: Chi-squared, reduced chi-squared, p-value
- `error_budget`: Breakdown of dominant error contributions

## Workflow

1. Identify error sources.
   - Classify each uncertainty as statistical (random) or systematic.
   - Determine if errors are correlated.

2. Propagate uncertainties.
   - For f(x_1, ..., x_n) with independent errors:
     sigma_f^2 = sum_i (partial f / partial x_i)^2 * sigma_i^2.
   - For correlated errors, use the full covariance matrix:
     sigma_f^2 = J^T C J, where J is the Jacobian and C the covariance matrix.
   - For highly nonlinear functions or complex correlations, use Monte Carlo propagation.

3. Fit data to a model (if applicable).
   - Minimize chi-squared: chi^2 = sum_i ((y_i - f(x_i; params)) / sigma_i)^2.
   - For correlated data: chi^2 = (y - f)^T C^{-1} (y - f).
   - Extract parameter uncertainties from the covariance matrix of the fit.

4. Assess goodness of fit.
   - Compute reduced chi-squared: chi^2_red = chi^2 / (N - p), where N is data points and p is parameters.
   - chi^2_red ~ 1 indicates a good fit; >> 1 suggests the model is inadequate or errors are underestimated; << 1 suggests errors are overestimated.

5. Report results.
   - Quote central value +/- statistical +/- systematic.
   - State confidence level (1-sigma = 68.3%, 2-sigma = 95.4%).

## Quality Checks

- Propagated errors must have the same dimensions as the quantity they describe.
- Reduced chi-squared should be O(1) for a valid fit.
- Parameter uncertainties from the fit should be consistent with the curvature of the chi-squared surface.
- Cross-check analytic propagation against Monte Carlo for nonlinear cases.

## Constraints

- Do not add systematic and statistical errors in quadrature unless they are genuinely independent.
- Do not interpret reduced chi-squared >> 1 as merely "a bad fit"; investigate whether the model or the error estimates are wrong.
- Gaussian error propagation is valid only when uncertainties are small compared to the scale over which the function is nonlinear; for large relative errors, use Monte Carlo.
- Always report what confidence level (1-sigma, 2-sigma, etc.) is used.
