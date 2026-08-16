# veil-dp-audit

Attack demonstrations and statistical DP verification for Veil Analytics.
This package does not implement or modify any privacy mechanism; it attacks
the mechanisms in `dp-core` from the outside, using only the query interface
an analyst would have, and reports what an attacker recovers. It exists to
make two things visible that no unit test shows on its own: *why* the
platform's noise matters, and whether a mechanism actually delivers the
epsilon it claims.

```bash
pip install -e ".[dev]"
python -m pytest tests -q
python -m dp_audit.run --epsilon 0.1,0.5,1.0,2.0 --output report
```

The CLI writes `report.json` and `report.md` (plain markdown tables, no
plotting library or external assets).

## The three attacks

### 1. Reconstruction attack (`dp_audit/reconstruction.py`)

A synthetic dataset carries a secret binary attribute per row. The attack
issues overlapping random-subset count queries -- "how many rows with the
secret attribute fall in this random subset" -- and solves the resulting
linear system by least squares to recover every row's secret bit.

The attack runs in **two modes**, and the contrast between them is the
point.

**Unbudgeted mode** (`run_unbudgeted_attack`) gives every query the same
fixed epsilon, no matter how many queries are issued. Against exact counts,
the attack reconstructs the secret bits with ~100% accuracy from nothing
but aggregate counts, no direct access to any row. Against
`dp_core.private_count` at a *single* fixed epsilon this stays low, but
at epsilon=2.0 with 200 queries a real run of this package measured
**70.5% accuracy** -- clearly above chance. That is not a mistake in
`dp_core`: it is the textbook Dinur-Nissim result (2003), that per-query
epsilon composes, and 200 queries at epsilon=2.0 have genuinely spent a
*total* of epsilon=400 -- a budget no policy on this platform would ever
grant. `reserve_privacy_budget` refuses further queries once a policy's
`epsilon_total` (5.0 by default) is exhausted, so in practice an analyst
gets two or three queries at epsilon=2.0 before the platform stops
answering, not the two hundred this mode gives the attacker for free.

**Budgeted mode** (`run_budgeted_attack`) is the demonstration that
actually matches the deployed guarantee: a fixed total epsilon (default
5.0, the platform's default policy budget) is split evenly across k
queries, so each one costs `epsilon_total / k`. This is exactly what
sequential composition buys -- asking more questions makes every answer
noisier in direct proportion. A real run swept k from 10 to 1000 at
`epsilon_total = 5.0` and measured reconstruction accuracy of
**0.500-0.620 across the entire sweep** (chance is 0.5), against the exact
baseline's 1.000. Accuracy did not climb with k -- more queries did not
help the attacker, because the budget, not the per-query cost, was held
fixed. This is the number that should be read as the headline result.

**What it does NOT prove:**
- The unbudgeted mode does not describe an attack the platform permits; it
  isolates what per-query epsilon leaks in the absence of any budget, which
  is real and worth knowing, but is not a claim about the deployed system.
- The budgeted mode's near-chance result is empirical, from one synthetic
  dataset design and one random-subset query strategy, at `epsilon_total =
  5.0`. It is not a proof that no query strategy or larger budget could do
  better -- a different `epsilon_total`, adaptively chosen subsets, or
  auxiliary information about the rows could change the picture. It also
  does not exercise the platform's actual `reserve_privacy_budget` code
  path (the Postgres RPC, its row locking, its rejection logic) -- it only
  reproduces the arithmetic that path enforces (`epsilon_total / k` per
  query) against the real mechanism.
- Neither mode tests any mechanism other than `private_count`, and both
  assume the attacker can choose subsets freely, which is a stronger
  capability than the platform's query interface actually grants an
  analyst.

### 2. Membership inference (`dp_audit/membership.py`)

A target record either is or is not in the dataset. The attacker has query
access to a count that the target would shift by exactly 1 if present (the
standard add/remove-one neighbouring-dataset pair). The attacker scores
membership directly from the released value and the result is reported as
AUC: the probability a random "in" release outranks a random "out" release.

Like the reconstruction attack, this runs in **two modes**.

**Unbudgeted mode** (`run_unbudgeted_membership_attack`): against exact
counts, AUC is 1.0 -- the attacker can always tell. Against
`dp_core.private_count` at a single fixed epsilon per trial with no shared
budget, a real run measured AUC climbing with epsilon: 0.508 at
epsilon=0.1, up to **0.817 at epsilon=2.0** across 500 repeated trials.
That climb is again a composition artifact -- 500 trials at epsilon=2.0
have spent a real total of epsilon=1000, nothing the platform would fund.

**Budgeted mode** (`run_budgeted_membership_attack`): a fixed total epsilon
(default 5.0) is split across the repeated trials the attack issues
against each world. A real run swept the trial count from 10 to 1000 at
`epsilon_total = 5.0` and measured AUC between **0.488 and 0.615 across the
whole sweep** -- near chance throughout, versus the exact baseline's 1.0.
Running more trials to average away the noise did not help, because each
trial got noisier in exact proportion to how many there were. This is the
number that matches what the platform actually enforces.

**What it does NOT prove:**
- It is a single, simple attack (count differencing) against a single
  mechanism. A real membership-inference adversary with auxiliary
  information, multiple correlated queries, or access to a different query
  type could do better; a low AUC here is not a certificate that no
  membership attack succeeds.
- The budgeted mode's near-chance result, like reconstruction's, is
  empirical at one `epsilon_total` and one query design, and does not
  exercise the platform's actual budget RPC -- only the arithmetic it
  enforces.
- AUC is measured against one specific `base_count`. Sensitivity of the
  attack to the base rate is not swept here.

### 3. Empirical epsilon estimation (`dp_audit/epsilon_estimation.py`)

For each release-path mechanism (`private_count`, `private_bounded_sum`,
`private_bounded_mean`), the module samples many outputs on a pair of
neighbouring datasets -- two inputs differing in exactly one row -- and
estimates the achieved epsilon from the empirical likelihood ratio: it
histograms the two output distributions and takes the largest observed
`|log(P_a / P_b)|` over bins, with each bin's proportion corrected by a
Bonferroni-adjusted Clopper-Pearson confidence interval so that finite-
sample noise (a bin that happens to get zero hits) cannot manufacture a
false violation.

**What it shows:** for every mechanism and every tested epsilon, the
estimated epsilon stays at or below the claimed epsilon (within the
tolerance parameter). A deliberately mis-calibrated mechanism -- one that
spends, say, 3x its claimed epsilon's worth of budget -- is reliably
flagged by the same code path (see
`TestDetectsAMiscalibratedMechanism` in `tests/test_epsilon_estimation.py`).

**What it does NOT prove -- this is the important one:**

`estimate_epsilon_from_samples` is a **statistical lower bound**, not a
certification. If the estimate exceeds the claimed epsilon beyond sampling
error, that is definite evidence of mis-calibration: some observed event's
corrected probability ratio genuinely exceeds what the claim permits. If
the estimate does **not** exceed the claimed epsilon, that proves nothing
about correctness -- it only means this particular audit, with this many
samples and this binning, on this one neighbouring-dataset pair, did not
find a violation. A rarer event, a different neighbour pair, finer bins, or
simply more samples could still find one. This is documented in the
module's docstring precisely so a passing report is never read as a proof
of safety.

It also does not audit anything about how `dp-core`'s mechanisms are
*used* -- it never calls the executor, the budget RPC, or any code outside
`dp-core` itself. A mechanism can be individually well-calibrated and still
be misused (wrong sensitivity passed in, budget not actually reserved,
wrong adjacency model) at the call site; none of that is in scope here.

## On randomness

Two different sources of randomness are used in this package, deliberately
kept separate:

- **Attack-side randomness** (synthetic secret bits, random query subsets,
  which world a trial samples from) uses `numpy.random.Generator`. This is
  not a privacy mechanism, so a fast, seedable PRNG is the right tool, and
  seeding it makes the exact-count baselines reproducible.
- **Mechanism randomness** is never touched here. Every call into
  `dp_core` samples through `secrets`, exactly as the platform does; this
  package never substitutes `numpy.random` or anything else for what a
  released value's noise actually is. That is also why DP-side attack
  results (accuracy, AUC, estimated epsilon) are not bit-for-bit
  reproducible run to run, only statistically stable -- the tests carry
  margin for that.

## Requests for changes outside this package

None required for this package to function. Everything needed
(`dp_core.private_count`, `private_bounded_sum`, `private_bounded_mean`)
is already on the `dp-core` public surface documented in
`packages/dp-core/README.md`.
