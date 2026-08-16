# dp-audit report

Generated: 2026-08-15T22:32:57.665396+00:00

Attack demonstrations and empirical epsilon verification for Veil Analytics' `dp-core` mechanisms. See `packages/dp-audit/README.md` for what each result does and does not prove -- in particular, none of this certifies that a mechanism is correctly calibrated; it can only catch mistakes.

Reconstruction and membership inference are each run in two modes: **unbudgeted** (fixed epsilon per query, no shared budget -- a real weakness of per-query epsilon in isolation, but a query volume the platform never actually funds) and **budgeted** (a fixed total epsilon of 5, matching the platform's default policy budget, split across every query the attack issues -- this is what the deployed system actually enforces, and is the headline result).

## Reconstruction attack

Overlapping random-subset count queries, solved as a linear system to recover a secret bit per row. Chance-level accuracy is 0.5 (each secret bit is an independent coin flip).

### Unbudgeted (fixed epsilon per query)

| condition | epsilon/query | rows | queries | accuracy |
|---|---|---|---|---|
| exact | exact | 200 | 200 | 1.000 |
| dp_unbudgeted | 0.1000 | 200 | 200 | 0.500 |
| dp_unbudgeted | 0.5000 | 200 | 200 | 0.565 |
| dp_unbudgeted | 1.0000 | 200 | 200 | 0.525 |
| dp_unbudgeted | 2.0000 | 200 | 200 | 0.575 |

### Budgeted (fixed total epsilon = 5, split across k queries)

| condition | epsilon total | k queries | epsilon/query | accuracy |
|---|---|---|---|---|
| exact | n/a | 200 | exact | 1.000 |
| dp_budgeted | 5 | 10 | 0.5000 | 0.615 |
| dp_budgeted | 5 | 50 | 0.1000 | 0.540 |
| dp_budgeted | 5 | 100 | 0.0500 | 0.620 |
| dp_budgeted | 5 | 200 | 0.0250 | 0.540 |
| dp_budgeted | 5 | 500 | 0.0100 | 0.500 |
| dp_budgeted | 5 | 1000 | 0.0050 | 0.525 |

Under a fixed total budget, accuracy should stay near chance across the whole k sweep -- more queries no longer help the attacker, because each one is noisier in exact proportion.

## Membership inference attack

Count-differencing attack on a target record present in one world and absent in the other. Chance-level AUC is 0.5.

### Unbudgeted (fixed epsilon per trial)

| condition | epsilon/trial | trials | AUC |
|---|---|---|---|
| exact | exact | 500 | 1.000 |
| dp_unbudgeted | 0.1000 | 500 | 0.508 |
| dp_unbudgeted | 0.5000 | 500 | 0.632 |
| dp_unbudgeted | 1.0000 | 500 | 0.686 |
| dp_unbudgeted | 2.0000 | 500 | 0.817 |

### Budgeted (fixed total epsilon = 5, split across trials)

| condition | epsilon total | trials | epsilon/trial | AUC |
|---|---|---|---|---|
| exact | n/a | 1000 | exact | 1.000 |
| dp_budgeted | 5 | 10 | 0.5000 | 0.615 |
| dp_budgeted | 5 | 50 | 0.1000 | 0.555 |
| dp_budgeted | 5 | 100 | 0.0500 | 0.513 |
| dp_budgeted | 5 | 200 | 0.0250 | 0.515 |
| dp_budgeted | 5 | 500 | 0.0100 | 0.522 |
| dp_budgeted | 5 | 1000 | 0.0050 | 0.488 |

## Empirical epsilon estimation

Histogram likelihood-ratio lower bound on the achieved epsilon for each release-path mechanism, sampled on a neighbouring-dataset pair (differing in exactly one row). This is a LOWER bound: it can prove a violation but never proves correctness.

| mechanism | claimed epsilon | estimated epsilon | samples | within claim |
|---|---|---|---|---|
| private_count | 0.10 | 0.000 | 6000 | yes |
| private_bounded_sum | 0.10 | 0.000 | 6000 | yes |
| private_bounded_mean | 0.10 | 0.000 | 6000 | yes |
| private_count | 0.50 | 0.315 | 6000 | yes |
| private_bounded_sum | 0.50 | 0.278 | 6000 | yes |
| private_bounded_mean | 0.50 | 0.169 | 6000 | yes |
| private_count | 1.00 | 0.726 | 6000 | yes |
| private_bounded_sum | 1.00 | 0.782 | 6000 | yes |
| private_bounded_mean | 1.00 | 0.258 | 6000 | yes |
| private_count | 2.00 | 1.661 | 6000 | yes |
| private_bounded_sum | 2.00 | 1.580 | 6000 | yes |
| private_bounded_mean | 2.00 | 0.663 | 6000 | yes |

