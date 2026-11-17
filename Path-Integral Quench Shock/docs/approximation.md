# The jump-diffusion approximation

Implementation: [`src/paolinelli_arioli.py`](../src/paolinelli_arioli.py) (`PaolinelliArioliModel`).
Decision record: D-007 in [`decisions.md`](decisions.md).

## 1. What the original model is (per the project brief)

Paolinelli & Arioli (2018) apply Ilinski's coherent-state path integral (gauge theory of
finance) to stock prices. The propagator

$$ K(x_f, T;\, x_i, 0) = \int \mathcal{D}x \; e^{\,iS[x]/\hbar} $$

has an action $S[x]$ with two parts:

- a **Gaussian part**, which gives geometric Brownian motion;
- a **non-linear perturbation** that models the effect of market orders, with strengths
  $\alpha_1, \alpha_2$.

In Euclidean (imaginary) time the weights become positive. The integral is then evaluated with
Metropolis–Hastings over hydrodynamic variables $(\rho, \varphi)$, using M = 100 coherent states
and $\tilde\beta = 2.5$ (paper Sec. 4, eqs. 6–7). With $\alpha_1 = \alpha_2 = 0$ the model reduces
to GBM.

> The full text of the paper was not available. Everything above comes from the project brief, and
> eqs. 6–7 were **not** implemented (L-012).

## 2. Definition of the approximation

The order perturbation is represented as compound-Poisson jumps on the log price
$x = \ln S$ (a Merton-type model):

$$ dx_t = m\,dt + \sigma_d\, dW_t + J\, dN_t, \qquad N_t \sim \text{Poisson}(\lambda t), \quad J \sim \mathcal N(\mu_J, \sigma_J^2) . $$

**Parametrization (all per minute):**

| Symbol | Definition | Meaning |
|---|---|---|
| σ | calibrated | **Total** 1-minute volatility (the empirical standard deviation) |
| λ | = α₁ | Jump intensity (jumps per minute) |
| σ_J | = α₂ · σ | Jump size, in units of σ |
| μ_J | = 0 | Symmetric jumps: no directional forecast |
| σ_d | = σ √(1 − α₁α₂²) | Diffusive volatility; requires α₁α₂² < 1 |
| m | = μ − σ_d²/2 − λ(e^{μ_J+σ_J²/2} − 1) | Drift that makes $E[S_T/S_0] = e^{\mu T}$; μ = 0 (martingale) |

**Properties that hold by construction:**

1. **GBM limit.** If α₁ = 0 or α₂ = 0, the model is exactly the GBM with the same σ. This is
   tested bit for bit.
2. **Equal variance.** $\operatorname{Var}(x_1) = \sigma_d^2 + \lambda\sigma_J^2 = \sigma^2$. The GBM
   baseline ("same σ, same T") therefore has the same variance, and any model/GBM difference
   comes from tail shape alone.
3. **Closed-form kurtosis.** Excess kurtosis is $\kappa_1 = 3\alpha_1\alpha_2^4$ at 1 minute and
   $\kappa_T = \kappa_1 / T$ at the horizon.
4. **Exact terminal law.** The horizon distribution is a Poisson-weighted mixture of normals:

   $$ P(x_T \le x) = \sum_{k\ge 0} \frac{e^{-\lambda T}(\lambda T)^k}{k!}\;
      \Phi\!\left(\frac{x - mT - k\mu_J}{\sqrt{\sigma_d^2 T + k\sigma_J^2}}\right) . $$

   `cdf`, `pdf` and `ppf` evaluate this directly. The sum is truncated where the Poisson tail falls
   below 10⁻¹⁴. Tail probabilities down to about 10⁻¹⁰ are therefore exact, which Monte Carlo
   with 1,000 stored samples could never resolve.
5. **Discretization.** `simulate_pdf` splits T into N steps and samples each increment exactly
   (a normal plus K ~ Poisson(λΔt) normal jumps). The terminal law does not depend on N; N only
   sets path resolution. The convergence check over N = 40/80/160 holds by construction (D-006).
6. **Determinism.** `simulate_pdf(seed=...)` is reproducible. `deterministic_seed(*parts)`
   derives seeds from SHA-256, so they are stable across processes, unlike Python's `hash()`.

## 3. What the approximation preserves

- **1-minute fat tails.** The calibrated model matches empirical excess kurtosis exactly and
  reduces the 1-minute KS distance 3–5× relative to GBM (F-001).
- **The GBM limit** when α₁ = α₂ = 0, which was the paper's sanity check.
- **The idea of an order-driven perturbation** with two strengths: one controls how often order
  shocks happen (α₁), the other how large they are (α₂).

## 4. What it loses

- **Overnight fat tails, if they exist.** The jumps are independent and identically distributed,
  so cumulants add and horizon kurtosis decays as 1/T. At T = 1050 the model is GBM to within
  ~1 % in the tail (F-003, F-005). Overnight fat tails of real prices, such as gaps from news or
  volatility regimes, are outside what this approximation can represent.
- **Memory and clustering.** Any path dependence that the coherent-state action may induce is
  absent here.
- **Asymmetry.** μ_J = 0 forces symmetric jumps. Empirical 1-minute skew in the calibration window
  is small (NVDA regular session +0.09), so little is lost at 1 minute.
- **Microscopic interpretation.** α₁ and α₂ here are phenomenological (frequency and size of
  jumps). They are not the coupling constants of the paper's action, so their numbers are **not
  comparable** with values reported in the paper.

## 5. Why this approximation and not the exact kernel

| Criterion | MH kernel (paper) | Jump-diffusion (chosen) |
|---|---|---|
| Specification available | No (eqs. 6–7 not accessible) | Yes |
| GBM limit at α = 0 | Yes | Yes (exact) |
| Fat tails at 1 minute | Yes (per paper) | Yes (calibrated) |
| Closed-form tail probabilities | No (MCMC estimate) | Yes |
| Cost of a calibration objective evaluation | High (MCMC per parameter point) | Milliseconds |
| Deterministic and auditable for shadow production | Hard | Easy |

## 6. What implementing the paper's kernel would require

Prerequisite: the full text of Paolinelli & Arioli (2018), especially Sec. 4 and eqs. 6–7.
After that, the work would be:

1. **Transcribe the Euclidean action** $S_E[\rho, \varphi]$ in hydrodynamic variables. This
   includes the exact functional form of the α₁ and α₂ terms and how $(\rho, \varphi)$ map to
   the observable log price.
2. **Discretize the path:** N time slices over T and M = 100 coherent states, with
   $\tilde\beta = 2.5$. Check how N and $\Delta t$ enter, and whether $\tilde\beta$ is
   dimensionless or tied to T.
3. **Metropolis–Hastings sampler** over path configurations: a proposal kernel (local slice
   updates, or a global update), the acceptance rule $\min(1, e^{-\Delta S_E})$, burn-in,
   thinning, and an integrated-autocorrelation diagnostic.
4. **Observable:** histogram the end-point $x_f$ to obtain $K(x_f, T; x_i, 0)$ as the PDF of log
   returns.
5. **Validation:** with α₁ = α₂ = 0, recover the analytic GBM propagator (KS test, as in the
   current inline tests). Reproduce a figure or table from the paper for Apple or the S&P 500 at
   1 minute.
6. **Calibration:** the objective needs an MCMC run per parameter point, so plan for noise-aware
   optimization (common random numbers, Bayesian optimization, or a coarse grid). The current
   frozen-parameter, versioning and shadow infrastructure stays unchanged. Only
   `approximation: "mh_kernel"` would be added.
7. **Key scientific question to answer:** does the coherent-state action induce **temporal
   dependence** between minutes? If not, F-005 applies unchanged and the overnight null result
   carries over to the exact kernel.
