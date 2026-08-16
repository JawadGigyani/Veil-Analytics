# Privacy Model

## Guarantee Boundary

Veil is a trusted-curator demonstration. Differential privacy limits inference from released analytics for analysts and other authorized consumers. It does not protect records from a malicious platform administrator, compromised host, cloud provider, or side channel.

The declared adjacency model is add or remove one privacy unit, and that unit
is now enforced rather than assumed.

A data owner may nominate an `entity_column` and a `max_contributions` bound at
ingestion. When set, the worker caps each entity's rows — dropping everything
beyond the k-th in a fixed deterministic order — *before* any aggregate runs,
and scales every sensitivity by k. The privacy unit becomes the entity.

When no entity column is nominated, `privacy_unit` stays `row` and
`max_contributions` is 1, which reproduces the historical behaviour exactly.
That case still carries the one-row-per-person assumption, and it is still the
owner's responsibility to uphold it: with no entity column there is nothing to
bound contributions by.

This matters more than it looks. Before contribution bounding, a dataset with
several rows per person made every epsilon ever spent against it wrong by a
factor of the largest per-person contribution — the noise was calibrated for a
sensitivity the data did not have. Nominating the entity column is the
difference between a guarantee and a hope.

## Mechanisms

- Count and histogram bucket counts use Laplace noise with sensitivity 1.
- Bounded sums clip each value to public bounds and use sensitivity `max(abs(lower), abs(upper))`.
- Bounded means use a split-epsilon bounded sum and noisy count, with a public minimum denominator and post-processing clamp to public bounds. This is a conservative approximate implementation; it must not be described as a formal substitute for a production-calibrated bounded-mean library without independent review.
- Grouped counts use noisy threshold selection and a configured maximum group count. Exact suppressed-group totals are not released.
- Top-k uses the same noisy threshold selection and releases at most the configured public limit. It is the grouped-count mechanism with a limit applied as post-processing, not an iterative noisy-max, so selecting the top k costs no extra epsilon.

### Grouped selection and suppression

Epsilon is split in half for a grouped release: one half decides which groups
clear the threshold, one half releases their counts. Groups partition the rows,
so each half composes in parallel across groups and the total cost is epsilon.

A group is released only when its noisy count clears the policy minimum by the
95% confidence radius of the selection noise:

```text
threshold = min_group_size + ln(20) / (epsilon / 2)
```

Comparing against `min_group_size` alone would barely suppress anything. At a
selection epsilon of 0.2 the Laplace scale is 5, so a group with a true count
of zero clears a bare threshold of 5 about 18% of the time. The margin drops
that below 2.5%, at the cost of also suppressing genuine groups that sit close
to the minimum. The threshold actually applied is returned with every grouped
release so the analyst can see what was required.

### Bounded mean uncertainty

The reported radius for a bounded mean covers the **sum-noise term only**:

```text
radius = ln(20) * (upper - lower) / ((epsilon / 2) * denominator)
```

where `denominator` is the noisy count floored at the public minimum. The
independent noise on the count contributes a further error term that this
figure does not include, and the post-processing clamp to the public bounds
truncates the tails. It is an indicative scale for the noise, not a calibrated
95% interval; the API tags it `range_basis: "sum_noise_only"` so the interface
can say so rather than implying a tighter guarantee than the mechanism gives.

On a small population with wide public bounds this radius exceeds the bound
range itself, meaning the released mean carries no information. Both the query
composer and the result panel warn when that is the case, because budget is
reserved before execution and is not refunded.

### Sensitive columns

A column marked `is_sensitive` may still be **measured** — a bounded sum, mean
or histogram over it releases a noisy aggregate like any other numeric field.
It may never be used as a `group_by` column or as a filter column.

The reason is that noise protects the *value*, not the *structure*. A group
label or a filter predicate names the attribute directly: releasing
"diagnosis = X: 412 ± 19" discloses that the queried population has diagnosis
X regardless of how much noise sits on the 412. This is the same reasoning
that keeps analyst filter values out of the audit log.

Enforced in three places: the Zod schema, the API route against real column
metadata, and independently in the worker from the `sensitive_columns` list
forwarded with every query.

### Row restrictions

A data owner may attach up to ten predicates to a dataset's privacy policy.
They are AND-ed into every query before aggregation, ahead of the analyst's
own filters, using the same parameterised and identifier-quoted compilation
path — there is no separate string-interpolation route for a restriction to
miss the existing filter hardening.

Restrictions **do not change sensitivity**. Removing one privacy unit moves an
aggregate by the same amount whether or not a fixed predicate narrowed the
rows first, so no noise calibration changes. This is a governance control, not
a differential-privacy one, and should not be described as strengthening the
guarantee.

Order of operations is entity contribution bounding, then restrictions, then
analyst filters. Restrictions only remove rows, so the per-entity cap still
holds after they apply.

A restriction never reaches an analyst. It is absent from responses, from
audit metadata (which records a count only), and from error messages — a
restriction referencing a column missing from the file fails with a static
message naming neither the column nor the value, because the predicate itself
may encode a sensitive attribute.

### Data independence of the error channel

Privacy budget is reserved before the worker runs, so once execution begins the
analyst is guaranteed a response. Every rejection inside the executor therefore
depends only on public metadata — query type, policy, owner-supplied bounds and
category domain. Nothing raises, changes the set of returned keys, or changes a
value's type based on what is in the file. A query whose filter matches no rows
is released through the same mechanism as any other, because "no rows matched"
is itself private, and an error conditioned on it would hand the analyst an
exact, un-noised bit that no amount of added noise can take back.

### Noise sampling

Randomness comes from `secrets`, but the *transform* matters as much as the
entropy. Naive floating-point sampling — an inverse-CDF Laplace draw or a
Box–Muller Gaussian draw — is attackable through the low-order bits of the
released value (Mironov, 2012) and can leak more than the nominal epsilon,
however good the entropy source is.

**Integer-valued releases no longer use that path.** Counts, grouped counts,
and histogram buckets go through the discrete Laplace (two-sided geometric)
mechanism in `dp_core.mechanisms.discrete`, which samples using only integer
and exact-rational (`fractions.Fraction`) arithmetic plus `secrets`. There is
no float rounding step in the sampling path for the attack to exploit. These
are the majority of the platform's releases and they are integer-valued
anyway, so nothing is lost by the change.

**Real-valued releases still use floating-point Laplace.** `bounded_sum` and
the sum term inside the bounded mean remain exposed to the same attack.
Closing that needs Mironov's snapping mechanism, which is not implemented —
its rounding and clamping are easy to get subtly wrong, and an unverified
security mechanism is worse than an honestly documented gap. Treat real-valued
releases as the weaker of the two paths.

### Gaussian releases and delta

Gaussian is available for counts, grouped counts, top-k, histograms, and
bounded sums. Sigma is calibrated with `calibrate_sigma_analytic`, the
Balle–Wang (2018) analytic mechanism, which binary-searches against the exact
Gaussian privacy profile rather than a bound and is therefore valid for every
epsilon. The classical Dwork–Roth calibration is retained under a separate
name and still refuses epsilon > 1; it is not on the release path.

Delta is genuinely spent. `reserve_privacy_budget` checks
`delta_used + requested <= delta_total` and increments `delta_used` inside the
same transaction and under the same policy row lock as epsilon, and writes
`delta_spent` to the append-only ledger. Gaussian was refused outright until
that was true, because an (ε, δ) mechanism running against a decorative delta
column is not (ε, δ)-differentially private in any meaningful sense.

Two rules are enforced at all three layers — the Zod schema, the API route,
and the worker: Gaussian requires delta > 0, and Laplace refuses delta > 0. A
Laplace release that spent delta would silently claim a weaker guarantee than
it delivers, and a Gaussian release with no delta is not a Gaussian mechanism.

**Gaussian is refused for the bounded mean.** That mechanism already splits
epsilon between a noisy sum and a noisy count; splitting delta across both as
well is a design decision that has not been justified here, so it is refused
rather than guessed at.

Note the residual gap: `gaussian_noise` is Box–Muller over floating point and
is therefore still exposed to the Mironov attack described above, unlike the
discrete-Laplace path used for integer counts. A discrete Gaussian is not
implemented.

### Measured cost of protecting the denominator

Benchmarking against `diffprivlib` at matched epsilon and sensitivity found
this platform's bounded mean carries roughly **2.3× the error** of
`diffprivlib.tools.mean`, consistently across epsilon.

This is a deliberate difference, not a calibration fault. `diffprivlib` treats
the array length as public and spends the whole epsilon on noising the clipped
sum. This platform cannot: the row count of a *filtered* query is itself
private — releasing it exactly would leak the size of the filtered
subpopulation — so epsilon is split between a noisy sum and a noisy count, and
the sum term alone therefore carries twice the noise scale.

The tradeoff is real and worth stating in both directions: the denominator is
protected, and the released mean is about 2.3× noisier for it. Counts and
bounded sums agree with both `diffprivlib` and `opendp` within sampling error,
which is what establishes that the gap is specific to this design choice and
not a general calibration problem.

Note the unexploited case: for an *unfiltered* mean the denominator is close to
public already, since `datasets.row_count` is published metadata. Spending the
full epsilon on the sum would be sound there — but only for a column declared
non-null at ingestion, because `count(column)` skips nulls and is otherwise
data-dependent. That optimisation is not implemented.

The enforced accountant is basic sequential composition:

```text
epsilon_used = sum(released and reserved epsilon)
epsilon_remaining = epsilon_total - epsilon_used
```

The privacy RPC locks the policy row and rejects non-positive epsilon, policy overflow, unauthorized actors, disallowed operations, and reservation conflicts. Reservations are made before worker execution. Failed executions are marked failed and keep their epsilon reservation.

Delta is recorded in the schema but never spent or checked. That is consistent
only because every mechanism that would consume it is refused. See
`docs/threat-model.md` for the full list of enforcement gaps.

The enforced counter is `privacy_policies.epsilon_used`; `privacy_ledger` is
the append-only evidence of how it got there. `GET /api/reports` recomputes the
ledger sum per dataset and reports any drift between the two.

## Public Bounds

Numeric bounds are owner-supplied metadata. The ingestion service never derives bounds from private file extrema. Numeric fields without valid public bounds cannot be measured.
