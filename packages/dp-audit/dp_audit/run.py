"""CLI entry point: run every dp-audit demonstration and emit a report.

    python -m dp_audit.run --epsilon 0.1,0.5,1.0,2.0 --output report

Writes ``<output>.json`` (structured results) and ``<output>.md`` (the same
results as markdown tables, no plotting library or external assets, so it
renders anywhere the JSON isn't convenient to read).

Both the reconstruction attack and the membership inference attack are run
in two modes, clearly labelled in both outputs:

- **Unbudgeted**: every query costs the same fixed epsilon regardless of
  how many queries are issued. This demonstrates a real, known weakness
  (Dinur-Nissim reconstruction defeats per-query epsilon given enough
  queries) but describes a query volume the platform would never actually
  fund -- `reserve_privacy_budget` refuses once a policy's total epsilon
  is spent.
- **Budgeted**: a fixed total epsilon (default 5.0, matching
  `privacy_policies.epsilon_total`'s default) is split across the queries
  the attack issues, exactly as sequential composition requires. This is
  the mode that matches what the platform actually enforces and should be
  read as the headline result.

Exit code is 1 if the epsilon estimation audit found a mechanism whose
empirical estimate exceeded its claimed epsilon beyond the stated tolerance
-- i.e. a probable mis-calibration -- and 0 otherwise. The reconstruction
and membership inference results are demonstrations, not pass/fail checks,
and never affect the exit code.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone

from dp_audit.epsilon_estimation import run_epsilon_estimation_suite
from dp_audit.membership import run_budgeted_suite as run_budgeted_membership_suite
from dp_audit.membership import run_unbudgeted_suite as run_unbudgeted_membership_suite
from dp_audit.reconstruction import run_budgeted_suite as run_budgeted_reconstruction_suite
from dp_audit.reconstruction import run_unbudgeted_suite as run_unbudgeted_reconstruction_suite

DEFAULT_EPSILON_TOTAL = 5.0  # matches privacy_policies.epsilon_total's platform default
DEFAULT_K_SWEEP = [10, 50, 100, 200, 500, 1000]


def parse_epsilons(raw: str) -> list[float]:
    values = [float(part) for part in raw.split(",") if part.strip()]
    if not values:
        raise ValueError("--epsilon must list at least one value")
    return values


def parse_int_list(raw: str) -> list[int]:
    values = [int(part) for part in raw.split(",") if part.strip()]
    if not values:
        raise ValueError("expected at least one comma-separated integer")
    return values


def build_report(
    epsilons: list[float],
    *,
    recon_rows: int,
    recon_queries: int | None,
    membership_trials: int,
    membership_base_count: int,
    epsilon_samples: int,
    epsilon_bins: int,
    epsilon_alpha: float,
    epsilon_tolerance: float,
    epsilon_total: float,
    k_sweep: list[int],
    seed: int | None,
) -> dict:
    reconstruction_unbudgeted = run_unbudgeted_reconstruction_suite(
        epsilons, n_rows=recon_rows, n_queries=recon_queries, seed=seed
    )
    reconstruction_budgeted = run_budgeted_reconstruction_suite(
        epsilon_total, k_sweep, n_rows=recon_rows, seed=seed
    )
    membership_unbudgeted = run_unbudgeted_membership_suite(
        epsilons, base_count=membership_base_count, n_trials=membership_trials
    )
    membership_budgeted = run_budgeted_membership_suite(
        epsilon_total, k_sweep, base_count=membership_base_count
    )
    epsilon_estimates = run_epsilon_estimation_suite(
        epsilons,
        n_samples=epsilon_samples,
        n_bins=epsilon_bins,
        alpha=epsilon_alpha,
        tolerance=epsilon_tolerance,
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "epsilons": epsilons,
        "parameters": {
            "reconstruction": {"n_rows": recon_rows, "n_queries": recon_queries or recon_rows},
            "membership_inference": {
                "n_trials": membership_trials,
                "base_count": membership_base_count,
            },
            "budgeted": {"epsilon_total": epsilon_total, "k_sweep": k_sweep},
            "epsilon_estimation": {
                "n_samples": epsilon_samples,
                "n_bins": epsilon_bins,
                "alpha": epsilon_alpha,
                "tolerance": epsilon_tolerance,
            },
        },
        "reconstruction_unbudgeted": [asdict(r) for r in reconstruction_unbudgeted],
        "reconstruction_budgeted": [asdict(r) for r in reconstruction_budgeted],
        "membership_inference_unbudgeted": [asdict(r) for r in membership_unbudgeted],
        "membership_inference_budgeted": [asdict(r) for r in membership_budgeted],
        "epsilon_estimation": [asdict(r) for r in epsilon_estimates],
    }


def _fmt_eps(value: float | None) -> str:
    return "exact" if value is None else f"{value:.4f}"


def render_markdown(report: dict) -> str:
    lines: list[str] = []
    lines.append("# dp-audit report")
    lines.append("")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append("")
    lines.append(
        "Attack demonstrations and empirical epsilon verification for Veil "
        "Analytics' `dp-core` mechanisms. See `packages/dp-audit/README.md` "
        "for what each result does and does not prove -- in particular, "
        "none of this certifies that a mechanism is correctly calibrated; "
        "it can only catch mistakes."
    )
    lines.append("")
    epsilon_total = report["parameters"]["budgeted"]["epsilon_total"]
    lines.append(
        f"Reconstruction and membership inference are each run in two modes: "
        f"**unbudgeted** (fixed epsilon per query, no shared budget -- a real "
        f"weakness of per-query epsilon in isolation, but a query volume the "
        f"platform never actually funds) and **budgeted** (a fixed total "
        f"epsilon of {epsilon_total:g}, matching the platform's default "
        f"policy budget, split across every query the attack issues -- this "
        f"is what the deployed system actually enforces, and is the headline "
        f"result)."
    )
    lines.append("")

    lines.append("## Reconstruction attack")
    lines.append("")
    lines.append(
        "Overlapping random-subset count queries, solved as a linear "
        "system to recover a secret bit per row. Chance-level accuracy is "
        "0.5 (each secret bit is an independent coin flip)."
    )
    lines.append("")
    lines.append("### Unbudgeted (fixed epsilon per query)")
    lines.append("")
    lines.append("| condition | epsilon/query | rows | queries | accuracy |")
    lines.append("|---|---|---|---|---|")
    for r in report["reconstruction_unbudgeted"]:
        lines.append(
            f"| {r['condition']} | {_fmt_eps(r['epsilon'])} | {r['n_rows']} "
            f"| {r['n_queries']} | {r['accuracy']:.3f} |"
        )
    lines.append("")
    lines.append(
        f"### Budgeted (fixed total epsilon = {epsilon_total:g}, split across k queries)"
    )
    lines.append("")
    lines.append("| condition | epsilon total | k queries | epsilon/query | accuracy |")
    lines.append("|---|---|---|---|---|")
    for r in report["reconstruction_budgeted"]:
        eps_total = "n/a" if r["epsilon_total"] is None else f"{r['epsilon_total']:g}"
        lines.append(
            f"| {r['condition']} | {eps_total} | {r['n_queries']} "
            f"| {_fmt_eps(r['epsilon'])} | {r['accuracy']:.3f} |"
        )
    lines.append("")
    lines.append(
        "Under a fixed total budget, accuracy should stay near chance across "
        "the whole k sweep -- more queries no longer help the attacker, "
        "because each one is noisier in exact proportion."
    )
    lines.append("")

    lines.append("## Membership inference attack")
    lines.append("")
    lines.append(
        "Count-differencing attack on a target record present in one world "
        "and absent in the other. Chance-level AUC is 0.5."
    )
    lines.append("")
    lines.append("### Unbudgeted (fixed epsilon per trial)")
    lines.append("")
    lines.append("| condition | epsilon/trial | trials | AUC |")
    lines.append("|---|---|---|---|")
    for r in report["membership_inference_unbudgeted"]:
        lines.append(
            f"| {r['condition']} | {_fmt_eps(r['epsilon'])} | {r['n_trials']} "
            f"| {r['auc']:.3f} |"
        )
    lines.append("")
    lines.append(
        f"### Budgeted (fixed total epsilon = {epsilon_total:g}, split across trials)"
    )
    lines.append("")
    lines.append("| condition | epsilon total | trials | epsilon/trial | AUC |")
    lines.append("|---|---|---|---|---|")
    for r in report["membership_inference_budgeted"]:
        eps_total = "n/a" if r["epsilon_total"] is None else f"{r['epsilon_total']:g}"
        lines.append(
            f"| {r['condition']} | {eps_total} | {r['n_trials']} "
            f"| {_fmt_eps(r['epsilon'])} | {r['auc']:.3f} |"
        )
    lines.append("")

    lines.append("## Empirical epsilon estimation")
    lines.append("")
    lines.append(
        "Histogram likelihood-ratio lower bound on the achieved epsilon for "
        "each release-path mechanism, sampled on a neighbouring-dataset "
        "pair (differing in exactly one row). This is a LOWER bound: it can "
        "prove a violation but never proves correctness."
    )
    lines.append("")
    lines.append("| mechanism | claimed epsilon | estimated epsilon | samples | within claim |")
    lines.append("|---|---|---|---|---|")
    for r in report["epsilon_estimation"]:
        verdict = "yes" if r["within_claim"] else "NO -- VIOLATION"
        lines.append(
            f"| {r['mechanism']} | {r['claimed_epsilon']:.2f} "
            f"| {r['estimated_epsilon']:.3f} | {r['n_samples']} | {verdict} |"
        )
    lines.append("")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dp_audit.run",
        description=(
            "Run reconstruction, membership inference, and empirical epsilon "
            "estimation attacks against dp-core and write a JSON + markdown report."
        ),
    )
    parser.add_argument(
        "--epsilon",
        default="0.1,0.5,1.0,2.0",
        help="Comma-separated per-query epsilon values for the unbudgeted mode (default: 0.1,0.5,1.0,2.0).",
    )
    parser.add_argument(
        "--output",
        default="report",
        help="Output path prefix; writes <output>.json and <output>.md (default: report).",
    )
    parser.add_argument("--seed", type=int, default=None, help="Seed for synthetic data generation.")
    parser.add_argument("--recon-rows", type=int, default=200, help="Reconstruction attack row count.")
    parser.add_argument(
        "--recon-queries",
        type=int,
        default=None,
        help="Unbudgeted reconstruction attack query count (default: equal to --recon-rows).",
    )
    parser.add_argument("--membership-trials", type=int, default=500, help="Unbudgeted membership inference trials per world.")
    parser.add_argument(
        "--membership-base-count", type=int, default=50, help="True count of the 'out' world for membership inference."
    )
    parser.add_argument(
        "--epsilon-total",
        type=float,
        default=DEFAULT_EPSILON_TOTAL,
        help=f"Total epsilon budget for the budgeted mode (default: {DEFAULT_EPSILON_TOTAL}, matching the platform's default policy budget).",
    )
    parser.add_argument(
        "--k-sweep",
        default=",".join(str(k) for k in DEFAULT_K_SWEEP),
        help=f"Comma-separated query/trial counts to sweep in budgeted mode (default: {','.join(str(k) for k in DEFAULT_K_SWEEP)}).",
    )
    parser.add_argument("--epsilon-samples", type=int, default=6000, help="Samples per mechanism per world.")
    parser.add_argument("--epsilon-bins", type=int, default=50, help="Histogram bins for epsilon estimation.")
    parser.add_argument("--epsilon-alpha", type=float, default=0.05, help="Confidence budget for epsilon estimation.")
    parser.add_argument(
        "--epsilon-tolerance",
        type=float,
        default=0.05,
        help="Slack added to the claimed epsilon before flagging a violation.",
    )
    args = parser.parse_args(argv)

    try:
        epsilons = parse_epsilons(args.epsilon)
        k_sweep = parse_int_list(args.k_sweep)
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    print(
        f"dp-audit: unbudgeted epsilon values {epsilons}; "
        f"budgeted total epsilon {args.epsilon_total} swept over k={k_sweep}",
        file=sys.stderr,
    )
    report = build_report(
        epsilons,
        recon_rows=args.recon_rows,
        recon_queries=args.recon_queries,
        membership_trials=args.membership_trials,
        membership_base_count=args.membership_base_count,
        epsilon_samples=args.epsilon_samples,
        epsilon_bins=args.epsilon_bins,
        epsilon_alpha=args.epsilon_alpha,
        epsilon_tolerance=args.epsilon_tolerance,
        epsilon_total=args.epsilon_total,
        k_sweep=k_sweep,
        seed=args.seed,
    )

    json_path = f"{args.output}.json"
    md_path = f"{args.output}.md"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(report))
    print(f"dp-audit: wrote {json_path} and {md_path}", file=sys.stderr)

    violations = [r for r in report["epsilon_estimation"] if not r["within_claim"]]
    if violations:
        print(
            f"dp-audit: WARNING -- {len(violations)} mechanism/epsilon pair(s) "
            "exceeded their claimed epsilon beyond sampling tolerance.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
