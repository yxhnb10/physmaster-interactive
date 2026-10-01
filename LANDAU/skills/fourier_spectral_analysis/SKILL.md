---
name: "fourier_spectral_analysis"
description: "Use when decomposing signals or fields into frequency/momentum components, applying Fourier transforms, or using spectral methods to solve differential equations."
---

# Fourier and Spectral Analysis

Apply this skill when the problem involves transforming between position/time and momentum/frequency domains, analyzing spectral content of signals or fields, or solving differential equations via spectral decomposition.

## Goal

Perform Fourier transforms (continuous or discrete), analyze spectral properties, and apply spectral methods to solve physical problems efficiently.

## Scope

- Continuous Fourier transform and its inverse (1D and multi-dimensional)
- Discrete Fourier transform (DFT) and Fast Fourier Transform (FFT)
- Power spectral density and Parseval's theorem
- Convolution theorem and filtering
- Spectral methods for solving ODEs and PDEs (Fourier, Chebyshev)
- Sampling theorem (Nyquist) and aliasing
- Windowing and spectral leakage in finite data sets
- Laplace and Z-transforms when appropriate

## Inputs

- `signal_or_field`: The function, data set, or field to be transformed
- `domain`: Spatial/temporal domain specification (extent, sampling rate, boundary conditions)
- `transform_type`: Which transform to apply (continuous FT, DFT/FFT, Laplace, etc.)
- `analysis_goal`: What to extract (frequency content, transfer function, spectral solution of PDE, etc.)

## Outputs

- `transformed_result`: The Fourier (or other spectral) transform of the input
- `spectral_analysis`: Power spectrum, dominant frequencies, bandwidth
- `spectral_solution`: Solution to a differential equation obtained via spectral methods
- `numerical_code`: Python implementation using numpy.fft or scipy.fft

## Workflow

1. Choose the appropriate transform.
   - Continuous data on infinite domain: continuous Fourier transform.
   - Discrete sampled data: DFT/FFT.
   - Periodic boundary conditions: Fourier series / FFT.
   - Non-periodic on finite domain: Chebyshev or sine/cosine transforms.

2. Apply the transform.
   - Use consistent sign and normalization conventions; state them explicitly.
   - Common physics convention: f_hat(k) = integral f(x) e^{-ikx} dx.
   - numpy convention: uses e^{-2 pi i k n / N} for DFT.

3. Analyze in the spectral domain.
   - Identify dominant frequencies/modes.
   - For PDEs: algebraic equations in Fourier space replace differential operators (d/dx -> ik).
   - Apply filters or solve algebraic equations as needed.

4. Transform back (if needed).
   - Apply the inverse transform.
   - Verify by comparing with the original (Parseval's theorem as a consistency check).

5. Handle numerical aspects.
   - Respect the Nyquist limit: maximum resolvable frequency = sampling_rate / 2.
   - Apply windowing (Hann, Hamming) to reduce spectral leakage for finite-length data.
   - Zero-pad for finer frequency resolution if needed.

## Quality Checks

- Parseval's theorem: total power in time/space domain must equal total power in frequency domain.
- The inverse transform of the forward transform must recover the original signal (within numerical precision).
- Nyquist criterion: the sampling rate must be at least 2x the highest frequency present.
- For spectral PDE solutions, verify against known analytic solutions or convergence under refinement.

## Constraints

- Always state the Fourier convention (sign, normalization) being used; mixing conventions is a common source of errors.
- Do not interpret FFT results beyond the Nyquist frequency; those are aliases.
- Spectral methods assume smoothness; for discontinuous solutions (shocks), use appropriate filtering or switch to finite-difference/finite-volume methods.
- When applying DFT, the data is implicitly assumed periodic; non-periodic data requires windowing or alternative transforms.
