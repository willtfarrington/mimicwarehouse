# EP-77 — Inference & group comparison

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-71 (Cross-sectional EDA module + page (Table 1)) · **Blocks:** EP-79 (GLM suite A: families + tidy()), EP-89 (Capstone #3), EP-116 (Dimensionality reduction & high-dimensional analysis)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 14 (*Statistical inference and group comparison*). EP-71's Table 1 reports
SMDs and no p-values by default; this brief adds the inferential layer — confidence intervals,
effect sizes, contingency methods, parametric/nonparametric tests, permutation tests, ANOVA and
multiplicity control — as one tidy API that EP-79's `tidy()` and EP-88's Inference page reuse.
Stack: scipy + statsmodels (standing decision), seeds per EP-36, suppression via `disclose`
(D-33). Repeated stays per subject violate independence: the module warns and points to the
cluster bootstrap (EP-78 adds a `boot_ci` adapter for these estimators). Theme per D-5: sepsis-3
versus non-sepsis first ICU stays across a panel of first-24 h features.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/stats/inference.py`** — `compare(df, outcome, group, kind="auto",
   method=None, ci=0.95, cluster=None)` → Polars tidy frame (outcome, groups, estimate, ci_low,
   ci_high, effect_size, test, statistic, p_value, n per group after `disclose.suppress`,
   warnings). Estimators: proportions (Wilson; Newcombe difference), risk difference / risk
   ratio / odds ratio with CIs, means (t / Welch; Cohen's d, Hedges' g), medians (Mann–Whitney;
   Cliff's delta; Hodges–Lehmann shift), Kruskal–Wallis, one-way + Welch ANOVA, chi-square /
   Fisher exact / Cochran–Mantel–Haenszel (stratified), paired McNemar / Wilcoxon, Cramér's V.
2. **Permutation tests**: `n_perm` default 5 000, `+1` corrected p, vectorised numpy, seed from
   the EP-36 policy; when `cluster` is given, cluster labels are permuted, not rows.
3. **Multiplicity**: `adjust(p, method="bonferroni"|"holm"|"fdr_bh"|"fdr_by")` via
   `statsmodels.stats.multitest`; panel-level adjusted p carried in the tidy frame.
4. **Guardrails**: groups with n < 11 → cells suppressed and test flagged `insufficient_n`;
   repeated subjects → warning; no auto-generated effect-size adjectives.
5. **Representative workflow**: first-ICU-stay adults, sepsis-3 (EP-42) vs not → ~20 first-24 h
   features (EP-55 mart) + in-hospital death (EP-75) → estimates + CIs, permutation p for a
   subset, BH-FDR across the panel → forest plot (`viz/` Altair spec) + Markdown report via EP-59
   (claim type *associational* — unadjusted group differences; the report says "not causal";
   retrospective statement).
6. **Tests** `tests/ep/test_ep77.py` (`@pytest.mark.ep_77`): known-answer checks against direct
   scipy/statsmodels calls; permutation determinism by seed; FDR monotonicity; suppression on a
   crafted small group; dev-tier run of the panel.

## Out of scope

- Regression adjustment → EP-79/80; cluster-bootstrap CIs → EP-78; Bayesian comparison → EP-117.
- High-dimensional FDR panels and regularised selection → EP-116; UI → EP-88 (Inference page).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_77` green on fixture + dev; `uv run --group dev mwh verify EP-77` green.
- Full-tier panel run as a logged background job (`uv run --group dev mwh build --tier full
  --select analysis.inference_sepsis_panel --background --job ep77-inference`); run id + wall
  time in the completion note; forest plot + report pass `mwh disclose check`.
- The tidy frame schema is documented and reused verbatim by EP-79 `tidy()`.

## Parked → final-roadmap.md

- Equivalence / TOST tests; large-scale permutation framework (INF-1).
