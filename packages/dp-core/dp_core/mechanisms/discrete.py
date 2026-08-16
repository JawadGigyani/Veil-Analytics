"""Discrete Laplace (two-sided geometric) mechanism for ε-differential privacy.

**The attack this defends against.**  Mironov (2012), "On significance of the
least significant bits for differential privacy", shows that a Laplace sampler
built from an inverse-CDF transform over IEEE-754 floats -- exactly what
``dp_core.mechanisms.laplace.laplace_noise`` does -- leaks information through
the low-order bits of the *released floating-point value*, no matter how good
the entropy source feeding it is.  The mechanism is:

1. Floats are not uniformly spaced.  The map from a uniform random draw to a
   rounded double has pre-images of wildly different sizes: some finite floats
   are the rounding target of many more real numbers than others.
2. ``log`` and the arithmetic around it round to the nearest representable
   double at every step, so the achieved output distribution is not the
   textbook Laplace distribution but a rounded approximation of it whose
   per-value probabilities depend on local float density.
3. An adversary who can see the released value exactly (not just an aggregate
   statistic derived from it) can use those per-value probability
   irregularities to distinguish neighbouring datasets with an advantage the
   epsilon accounting never budgeted for -- i.e. the *true* privacy loss of
   the floating-point sampler is strictly larger than the epsilon it claims.

**What this module does about it.**  For integer-valued releases (counts,
histogram buckets) there is no need to go anywhere near a float: the discrete
Laplace distribution --  also called the two-sided geometric distribution --
is a first-class ε-DP mechanism over the integers, no rounding involved
anywhere in the sampling path.  Every random choice below is a comparison
between an integer drawn from ``secrets`` and an exactly-represented rational
bound (via :class:`fractions.Fraction`), so the returned integer's exact
probability matches the theoretical PMF -- there is no float rounding step
that a released value could leak through.

The one place a float still enters is the *scale* argument itself: callers
pass ``sensitivity / epsilon`` as an ordinary Python float.  ``Fraction(scale)``
converts that float to the exact rational equal to its IEEE-754 value (not an
approximation -- floats already *are* rationals with a power-of-two
denominator), so the parameter is exact and only the usual "epsilon itself is
a float" imprecision remains. That is a property of the API contract shared
with every other mechanism in this package, not a new leak.

**What this module does not do.**  It does not address real-valued releases.
``private_bounded_sum`` and the sum term inside
``private_bounded_mean_release`` still return a float sampled via
``laplace_noise``'s inverse-CDF transform, and Mironov's attack still applies
to them. Fixing that requires the snapping mechanism (Mironov 2012, Section
5.2), which rounds the *input* to a power-of-two grid before adding noise so
that floating-point rounding cannot depend on secret data. It is not
implemented here: getting the rounding and clamping exactly right is easy to
get subtly wrong, and shipping an unverified version of a security mechanism
is worse than clearly declaring the gap. Real-valued sums remain subject to
the floating-point caveat documented in the package README.

## Algorithm

Sampling follows Canonne, Kamath, and Steinke, "The Discrete Gaussian for
Differential Privacy" (2020), Algorithm 2, which is originally due to
von Neumann's exact algorithm for Bernoulli(exp(-x)):

1. ``_sample_bernoulli(p, q)`` -- exact Bernoulli(p/q) for integers
   0 <= p <= q, via rejection sampling of a uniform integer in [0, q) from
   ``secrets.randbits``.
2. ``_sample_bernoulli_exp1(p, q)`` -- exact Bernoulli(exp(-p/q)) for
   0 <= p/q <= 1, using von Neumann's alternating-series trick: run
   independent Bernoulli(x/1), Bernoulli(x/2), ... trials until the first
   failure at trial K; the output is True iff K is odd. This is an exact
   sampler, not an approximation -- the identity
   sum_{k=0}^∞ (-1)^k x^k / k! = e^{-x} makes P(K odd) equal exp(-x) exactly.
3. ``_sample_bernoulli_exp(p, q)`` -- extends (2) to p/q > 1 by peeling off
   whole factors of exp(-1) before applying (2) to the fractional remainder.
4. ``_sample_geometric(p, q)`` -- counts successes of (3) before the first
   failure, giving the one-sided geometric distribution
   P(G = k) = (1 - t) * t^k with t = exp(-p/q).
5. ``discrete_laplace_noise`` combines a magnitude from (4) with a fair-coin
   sign, rejecting and resampling the (magnitude=0, sign=negative) case so
   that zero is not double-counted (-0 and +0 are the same integer). This
   produces the standard two-sided geometric PMF exactly.

This sampler runs in O(scale) expected trials, dominated by step 4. That is
fine for the per-record epsilon values this platform uses (order 0.01-10),
but it is not the sub-linear "doubling" construction from the same paper, so
it should not be used for extremely small epsilon (e.g. epsilon far below
0.001) without revisiting that cost.
"""

from __future__ import annotations

import secrets
from fractions import Fraction


def _sample_bernoulli(p: int, q: int) -> bool:
    """Exact Bernoulli(p/q) for integers 0 <= p <= q, q > 0.

    Draws a uniform integer in [0, q) by rejection sampling on random bits
    (reject draws >= q so every accepted value is exactly uniform), then
    compares it against p.  No float is ever produced.
    """
    if p == 0:
        return False
    if p == q:
        return True
    bit_length = q.bit_length()
    while True:
        r = secrets.randbits(bit_length)
        if r < q:
            return r < p


def _sample_bernoulli_exp1(p: int, q: int) -> bool:
    """Exact Bernoulli(exp(-p/q)) for integers 0 <= p <= q, q > 0.

    Von Neumann's exact algorithm: run Bernoulli(x/1), Bernoulli(x/2), ...
    (x = p/q) until the first failure at trial K; return True iff K is odd.
    """
    k = 1
    while _sample_bernoulli(p, q * k):
        k += 1
    return k % 2 == 1


def _sample_bernoulli_exp(p: int, q: int) -> bool:
    """Exact Bernoulli(exp(-p/q)) for any integers p >= 0, q > 0.

    Peels off whole units of exp(-1) (each of which must independently
    succeed) and finishes with one call to ``_sample_bernoulli_exp1`` on the
    fractional remainder, using exp(-p/q) = exp(-1)^floor(p/q) * exp(-frac).
    """
    while p > q:
        if not _sample_bernoulli_exp1(q, q):
            return False
        p -= q
    return _sample_bernoulli_exp1(p, q)


def _sample_geometric(p: int, q: int) -> int:
    """Sample from the one-sided geometric distribution on {0, 1, 2, ...}.

    P(G = k) = (1 - t) * t^k where t = exp(-p/q). Implemented as the number
    of successful Bernoulli(t) trials observed before the first failure.
    """
    count = 0
    while _sample_bernoulli_exp(p, q):
        count += 1
    return count


def discrete_laplace_noise(scale: float) -> int:
    """Sample an integer from the discrete Laplace (two-sided geometric) distribution.

    P(X = k) = ((1 - t) / (1 + t)) * t^|k|, for all integers k, where
    t = exp(-1/scale).  This is the discrete analogue of Laplace(0, scale):
    same exponential tail decay, but every draw and every intermediate
    comparison is over integers or exact rationals, so nothing here goes
    through IEEE-754 rounding the way ``laplace.laplace_noise`` does.

    ``scale`` is converted to a :class:`fractions.Fraction` via its exact
    IEEE-754 value (not a rounded approximation of it), so the sampling
    parameter is exact even though the caller supplies it as a float.
    """
    if scale <= 0:
        raise ValueError("Scale must be positive")
    # 1/t = exp(1/scale)'s exponent argument, kept as an exact rational p/q.
    inv_t = Fraction(1) / Fraction(scale)
    p, q = inv_t.numerator, inv_t.denominator
    while True:
        magnitude = _sample_geometric(p, q)
        positive = _sample_bernoulli(1, 2)
        if magnitude == 0 and not positive:
            # Reject to avoid double-counting zero (+0 and -0 are the same
            # integer): without this, P(X=0) would be twice the true value.
            continue
        return magnitude if positive else -magnitude


def private_count_discrete(
    value: int,
    epsilon: float,
    max_contributions: int = 1,
) -> int:
    """Release a differentially private count using the discrete Laplace mechanism.

    Unlike ``laplace.private_count``, no floating-point noise value is ever
    produced -- the noise is an integer from ``discrete_laplace_noise`` and the
    sum is exact integer addition, so Mironov's low-order-bit attack on the
    released value does not apply.

    Sensitivity is ``max_contributions``: under add/remove-one-entity
    adjacency, removing an entity that contributed up to k rows moves the count
    by up to k.  The default of 1 is the add/remove-one-row special case.

    The result is clamped to be non-negative, same as ``private_count``.
    """
    if epsilon <= 0:
        raise ValueError("Epsilon must be positive")
    if max_contributions < 1:
        raise ValueError("max_contributions must be at least 1")
    return max(0, value + discrete_laplace_noise(max_contributions / epsilon))
