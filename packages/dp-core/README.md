# veil-dp-core

Differential privacy mechanisms, sensitivity calculations, and composition
accounting for Veil Analytics.

## What is on the release path

Only these are called when the platform answers a query. Everything else in
this package is library surface — implemented and tested, but not currently
reachable from a release. That distinction is stated here rather than left for
a reader to discover by grepping for callers.

| Symbol | Used by |
|---|---|
| `discrete_laplace_noise` | grouped-count selection threshold |
| `private_count_discrete` | counts, grouped counts, histogram buckets |
| `laplace_noise` | the real-valued mechanisms below |
| `private_bounded_sum` | bounded sums |
| `private_bounded_mean_release` | bounded means (returns the denominator and radius) |
| `calibrate_sigma_analytic` | every Gaussian release (Balle-Wang, valid for all epsilon) |
| `private_count_gaussian_analytic` | Gaussian counts, grouped counts, histogram buckets |
| `private_sum_gaussian_analytic` | Gaussian bounded sums |
| `confidence_radius` | reported uncertainty, and the group-suppression margin |
| `bounded_sum_sensitivity` | bounded sums |

`private_bounded_mean` is a thin wrapper over `private_bounded_mean_release`
kept for callers that only want the value.

## Library surface, not on the release path

These are correct and tested, but nothing in `services/analytics-worker`
calls them today.

- **`private_count`** — the original floating-point count mechanism. Superseded
  on the release path by `private_count_discrete`; kept because it is the
  reference the benchmark compares against and because removing it would
  silently change the meaning of existing tests.
- **Gaussian, classical calibration** — `calibrate_sigma`,
  `private_count_gaussian`, `private_sum_gaussian`. Superseded on the release
  path by the `*_analytic` variants; `calibrate_sigma` keeps its epsilon <= 1
  guard and is retained as the reference the analytic calibration is compared
  against.
- **Randomized response** — `randomized_response`,
  `aggregate_randomized_responses`, `estimate_frequencies`. Local-model
  mechanisms. The platform is a trusted-curator design, so there is no place
  in the current architecture where a per-record local mechanism belongs; an
  endpoint that took raw values from the caller was removed because it
  inverted the trust model.
- **Exponential mechanism and noisy max** — `exponential_mechanism`,
  `noisy_argmax`, `private_top_k`. The platform's `top_k` query type does
  **not** use `private_top_k`. It reuses the grouped-count path and takes the
  top k as post-processing, which costs nothing extra in privacy, whereas
  `private_top_k` splits epsilon across k sequential selections. The
  grouped-count route is the better mechanism here, so `private_top_k` stays
  unused on purpose.
- **Accounting** — `sequential_composition`, `remaining_budget`,
  `advanced_composition`, `gaussian_composition`. The enforced accountant is
  sequential composition inside the `reserve_privacy_budget` PostgreSQL
  function, with a mirror of the sequential and advanced formulas in
  `src/lib/accounting.ts` for the privacy report. The Python accounting module
  is used by tests and benchmarks.
- **Sensitivity** — `count_sensitivity`, `histogram_sensitivity`,
  `bounded_mean_sensitivity`, `grouped_count_sensitivity`. Counts and
  histogram buckets have sensitivity 1 under add/remove-one and the executor
  uses that directly.

## Known restrictions

**`calibrate_sigma` still rejects epsilon > 1** — that guard is intentional
and stays. It implements the classical Dwork–Roth bound,
σ = Δ·√(2·ln(1.25/δ))/ε, which is only proven for ε ≤ 1; above that it
understates the required noise and the release would not be (ε, δ)-DP.

Gaussian releases are enabled as of migration 007, which makes
`reserve_privacy_budget` spend and check delta under the same row lock epsilon
already gets. They remain refused for the **bounded mean**: that mechanism
splits epsilon across a noisy sum and a noisy count, and splitting delta across
both as well is a design decision that has not been justified, so it is
refused rather than guessed at.

**Fixed:** `calibrate_sigma_analytic(sensitivity, epsilon, delta)` implements
the analytic Gaussian mechanism of Balle and Wang (2018). It binary-searches
σ directly against the exact Gaussian privacy profile
`δ(ε) = Φ(Δ/(2σ) - εσ/Δ) - exp(ε)·Φ(-Δ/(2σ) - εσ/Δ)` (not a bound — the
tight expression), so it is valid for **every** ε > 0. For ε ≤ 1 it never
returns a σ larger than `calibrate_sigma`'s; above ε = 1, where the classical
function refuses outright, it still returns a valid, verified σ. Both
functions are kept, under clearly different names, so a caller has to choose
deliberately, and `calibrate_sigma_analytic` is the one every Gaussian release
on the platform goes through. `gaussian_composition` still inverts the
classical bound and carries the ε ≤ 1 limit; it is diagnostic only and is not
on the release path.

**Noise sampling: fixed for integer-valued releases, still open for
real-valued ones.** `laplace_noise` uses an inverse-CDF transform and
`gaussian_noise` uses Box-Muller; both are attackable through the low-order
bits of the released *floating-point* value (Mironov, 2012), regardless of the
quality of the entropy feeding them.

Counts, grouped counts, and histogram buckets — the majority of the platform's
releases, and integer-valued anyway — now go through
`dp_core.mechanisms.discrete`. `discrete_laplace_noise(scale)` samples the
discrete Laplace (two-sided geometric) distribution using only integer and
exact-rational (`fractions.Fraction`) arithmetic plus `secrets`, following
Canonne-Kamath-Steinke (2020); there is no float rounding step in the sampling
path for the attack to exploit. `private_count_discrete` is the drop-in
analogue of `private_count` built on it, and is what
`services/analytics-worker/app/executor.py` calls.

`private_bounded_sum` and the sum term inside `private_bounded_mean_release`
are real-valued and still go through `laplace_noise`, so they remain subject
to this. Mironov's snapping mechanism, which would close the same gap for
them, is **not implemented** — getting its rounding and clamping exactly right
is easy to get subtly wrong, and an unverified security mechanism is worse
than an honestly documented gap. See `dp_core/mechanisms/discrete.py`'s module
docstring for the full attack description.

**`private_bounded_mean_release.confidence_radius` covers the sum-noise term
only.** The independent noise on the count contributes further error, and the
post-processing clamp to the public bounds truncates the tails. It is an
indicative scale, not a calibrated 95% interval, and the API tags it
`range_basis: "sum_noise_only"` so the interface can say so.

## Adjacency model

Sensitivity is calculated for add/remove-one-**entity** adjacency, where one
entity may contribute at most `max_contributions` rows. Every mechanism and
sensitivity function takes `max_contributions` (default 1) and scales by it:

| Quantity | Sensitivity |
|---|---|
| count, histogram bucket, per-group count | `k` |
| bounded sum | `k × max(\|lower\|, \|upper\|)` |
| bounded mean, shifted-sum term | `k × (upper − lower)` |
| bounded mean, count term | `k` |

Add/remove-one-**row** adjacency is the special case `k = 1`, which is what the
defaults give, so a caller that ignores the parameter gets the historical
behaviour unchanged.

Enforcement lives above this package: the worker caps each entity's rows via
`query_ir.compiler.compile_entity_bounded_source` before any aggregate runs, so
`max_contributions` is a real bound rather than a declared one. A dataset
without a nominated entity column keeps `privacy_unit = "row"` on the privacy
policy — in that case there is no column to bound contributions by, and the
one-row-per-person assumption still applies and is still the caller's to
uphold.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest tests -q
```

Or run every suite in the repository with `python scripts/run_tests.py` from
the repository root.
