import math

import pytest

from app import privacy
from dp_core.mechanisms import laplace as dp_laplace


@pytest.mark.parametrize("noise", [-10_000.0, -0.1, 0.0, 10_000.0])
def test_private_count_is_always_non_negative(monkeypatch, noise):
    monkeypatch.setattr(dp_laplace, "laplace_noise", lambda scale: noise)
    monkeypatch.setattr(privacy, "laplace_noise", lambda scale: noise)

    assert privacy.private_count(3, epsilon=0.5) >= 0


@pytest.mark.parametrize(
    ("value", "noise", "expected"),
    [
        (-50.0, 0.0, 0.0),
        (150.0, 0.0, 100.0),
        (50.0, -10_000.0, 0.0),
        (50.0, 10_000.0, 100.0),
        (42.125, 0.0, 42.12),
    ],
)
def test_bounded_mean_stays_within_bounds(monkeypatch, value, noise, expected):
    monkeypatch.setattr(dp_laplace, "laplace_noise", lambda scale: noise)
    monkeypatch.setattr(privacy, "laplace_noise", lambda scale: noise)

    result = privacy.private_bounded_mean(value * 10, count=10, epsilon=0.5, lower=0, upper=100, minimum_denominator=10)

    assert 0 <= result <= 100
    if noise == 0:
        assert result == expected
    assert 0 <= result <= 100


@pytest.mark.parametrize("random_bits", [0, 1 << 52, (1 << 53) - 1])
def test_laplace_is_finite_at_random_generator_edge_values(monkeypatch, random_bits):
    import secrets
    monkeypatch.setattr(secrets, "randbits", lambda bits: random_bits)

    assert math.isfinite(privacy.laplace(scale=2.0))
