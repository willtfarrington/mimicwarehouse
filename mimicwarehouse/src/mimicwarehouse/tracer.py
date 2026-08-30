"""Tracer bullet: first-ICU-stay adults -> in-hospital mortality (EP-31; D-5, D-8).

The first end-to-end proof over the tier catalogs: hand-written cohort SQL
(``sql/tracer_first_icu_mortality.sql``, a CTE chain with one step per criterion), every
number a human or a session sees pulled through :func:`mimicwarehouse.safe.safe_query`
(D-31, ``actor="tracer"``), a plain in-process logistic regression, and a Markdown report
with attrition counts under ``runs/tracer/<yyyymmddThhmmss>-<tier>/``. It deliberately
does **not** build the cohort engine (EP-46/47), the run ledger (EP-35) or the report
engine (EP-130) — it shows what they must make routine.

The three public services and the runner:

* :func:`attrition` — one ``safe_query("SELECT count(*) AS n FROM <step>")`` per CTE step
  (:data:`STEPS`); a count suppressed by the k = 11 rule comes back as ``n = None``.
* :func:`descriptives` — mortality by age band (:data:`AGE_BANDS`) x gender and by
  ``first_careunit``, each as ``count(*) AS n`` plus
  ``count(*) FILTER (WHERE hospital_expire_flag = 1) AS n_deaths`` — a genuine count
  column, so the row-wise k = 11 suppression applies to deaths too (``rows_suppressed``
  is recorded). (The brief's ``sum(hospital_expire_flag)`` returns HUGEINT, which the
  safe-query AST walk cannot re-cast without tripping the aggregate-only rule; the
  FILTER form keeps the same value as a BIGINT count.)
* :func:`fit` — reads the ``cohort`` CTE through
  :func:`~mimicwarehouse.catalog.connect.open_catalog` (READ_ONLY, in-process only; the
  frame is never printed or written) into polars -> pandas -> statsmodels ``Logit``
  (treatment coding, ``cov_type="HC1"``); odds ratios with 95 % CIs, n, events, and the
  in-sample AUC (labelled *in-sample, optimistic*). A constant outcome (possible on the
  fixture) records ``not_fit (constant outcome)`` instead of raising. A categorical
  level with zero events / zero non-events (quasi-separation — real on **every** tier:
  a handful of rare ``first_careunit`` / ``admission_type`` values have no deaths) has
  no finite ML confidence interval; its rows are excluded — which fits the remaining
  coefficients exactly (their profile likelihood equals the row-excluded likelihood) —
  and the degenerate levels are named in ``model.json`` and the report. Residual
  optimizer failures record ``not_fit`` with the reason.
* :func:`run_tracer` — writes ``manifest.json`` (git sha, package + DuckDB versions,
  tier, ``core_snapshot_id``, params, cohort n, wall_s, the audit ids of every
  ``safe_query`` call), ``attrition.json``, ``descriptives.json``, ``model.json`` and
  ``report.md``. Raw-int JSON stays under ``runs/`` (FC-16); the report's integers go
  through :func:`mimicwarehouse.inventory.fmt_int`. Claim type: **associational
  (exploratory)**; retrospective; no prediction claim (that is P7, EP-110).

Nothing under the run folder may carry an identifier column name or a value longer than
64 characters — model term labels are normalised to ``<var>=<level>`` and truncated.
Time-semantics notes for EP-34: the age/era logic (``anchor_age + (year(admittime) -
anchor_year)``, cap 91; ``anchor_year_group`` as era covariate) is inlined here.

CLI: ``mwh tracer --tier {fixture,demo,dev,full} [--background --job NAME]`` (attached in
:mod:`mimicwarehouse.cli`; ``--background`` reuses :func:`mimicwarehouse.dag.jobs.launch`).
Import budget: duckdb / polars / pandas / statsmodels are imported inside the function
bodies (cli.py rule).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.markup import escape

from mimicwarehouse.config import Settings, Tier, get_settings
from mimicwarehouse.console import console

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cli import CliState

#: Audit actor of every tracer safe_query call (the acceptance counts these lines).
ACTOR = "tracer"

#: CTE steps of ``sql/tracer_first_icu_mortality.sql``, in attrition order.
STEPS: tuple[str, ...] = ("base", "first_stay", "adult", "complete", "cohort")

#: Age bands of the descriptives (upper bounds exclusive; the cohort floor is 18).
AGE_BANDS: tuple[tuple[str, int | None], ...] = (
    ("18-39", 40),
    ("40-64", 65),
    ("65-79", 80),
    ("80+", None),
)

#: The model formula (treatment coding via C(); age stays linear).
MODEL_FORMULA = (
    "hospital_expire_flag ~ age_at_admit + C(gender) + C(admission_type) "
    "+ C(first_careunit) + C(anchor_year_group)"
)

#: Longest string value allowed in any run-folder file (mirrors safe.FREE_TEXT_MAX_CHARS).
VALUE_MAX_CHARS = 64

CLAIM_TYPE = "associational (exploratory)"
RETROSPECTIVE_SENTENCE = "MIMIC-IV analyses are retrospective."

TIERS = ("fixture", "demo", "dev", "full")

SQL_FILENAME = "tracer_first_icu_mortality.sql"


class TracerError(RuntimeError):
    """A tracer-specific failure (bad step name, unwritable run folder)."""


@dataclass(slots=True)
class TracerResult:
    """One tracer run: where it wrote and the four JSON payloads."""

    run_id: str
    tier: str
    out_dir: Path
    attrition: list[dict[str, Any]]
    descriptives: dict[str, Any]
    model: dict[str, Any]
    manifest: dict[str, Any]


# ---------------------------------------------------------------------------
# Cohort SQL
# ---------------------------------------------------------------------------


@cache
def cohort_cte_sql() -> str:
    """The packaged WITH-clause chain (module docstring; not executable on its own)."""
    return (files(__package__) / "sql" / SQL_FILENAME).read_text(encoding="utf-8")


def statement(select: str) -> str:
    """The CTE chain plus one final ``SELECT`` over a step or the cohort."""
    return f"{cohort_cte_sql().rstrip()}\n{select}"


def _age_band_case() -> str:
    parts = []
    for label, upper in AGE_BANDS:
        if upper is None:
            parts.append(f"ELSE '{label}'")
        else:
            parts.append(f"WHEN age_at_admit < {upper} THEN '{label}'")
    return "CASE " + " ".join(parts) + " END"


# ---------------------------------------------------------------------------
# Attrition + descriptives (everything through safe_query; D-31)
# ---------------------------------------------------------------------------


def attrition(tier: Tier | str, *, settings: Settings | None = None) -> list[dict[str, Any]]:
    """One suppressed ``count(*)`` per CTE step: ``[{step, n, audit_id}]`` in
    :data:`STEPS` order; ``n`` is None when the k = 11 rule suppressed the count."""
    from mimicwarehouse.safe import safe_query

    settings = settings or get_settings()
    rows: list[dict[str, Any]] = []
    for step in STEPS:
        result = safe_query(
            statement(f"SELECT count(*) AS n FROM {step}"),
            tier=tier,
            actor=ACTOR,
            settings=settings,
        )
        n = int(result.df["n"][0]) if result.df.height == 1 else None
        rows.append({"step": step, "n": n, "audit_id": result.audit_id})
    return rows


def descriptives(tier: Tier | str, *, settings: Settings | None = None) -> dict[str, Any]:
    """Suppressed mortality tables (module docstring): ``by_age_gender`` and
    ``by_first_careunit``, each ``{rows, rows_suppressed, audit_id}``; plus ``k``."""
    from mimicwarehouse.safe import K_FLOOR, safe_query

    settings = settings or get_settings()
    counts = "count(*) AS n, count(*) FILTER (WHERE hospital_expire_flag = 1) AS n_deaths"
    queries = {
        "by_age_gender": statement(
            f"SELECT {_age_band_case()} AS age_band, gender, {counts} "
            "FROM cohort GROUP BY 1, 2 ORDER BY 1, 2"
        ),
        "by_first_careunit": statement(
            f"SELECT first_careunit, {counts} FROM cohort GROUP BY 1 ORDER BY 1"
        ),
    }
    out: dict[str, Any] = {"k": K_FLOOR}
    for name, sql in queries.items():
        result = safe_query(sql, tier=tier, actor=ACTOR, settings=settings)
        out[name] = {
            "rows": result.df.to_dicts(),
            "rows_suppressed": result.rows_suppressed,
            "audit_id": result.audit_id,
        }
    return out


# ---------------------------------------------------------------------------
# Model (in-process read; never printed)
# ---------------------------------------------------------------------------


def _term_label(term: str) -> str:
    """patsy ``C(var)[T.level]`` -> ``var=level``, truncated to
    :data:`VALUE_MAX_CHARS` (run-folder rule)."""
    label = term
    if label.startswith("C(") and "[T." in label:
        var, _, level = label.partition(")[T.")
        label = f"{var.removeprefix('C(')}={level.removesuffix(']')}"
    return label[:VALUE_MAX_CHARS]


def fit(tier: Tier | str, *, settings: Settings | None = None) -> dict[str, Any]:
    """Fit the logistic regression on the ``cohort`` CTE (module docstring); returns the
    ``model.json`` payload (aggregate statistics only, never rows)."""
    from mimicwarehouse.catalog.connect import open_catalog

    settings = settings or get_settings()
    con = open_catalog(tier, settings=settings)
    try:
        frame = con.execute(
            statement(
                "SELECT age_at_admit, gender, admission_type, first_careunit, "
                "anchor_year_group, hospital_expire_flag FROM cohort"
            )
        ).pl()
    finally:
        con.close()

    n = frame.height
    n_events = int(frame["hospital_expire_flag"].sum()) if n else 0
    payload: dict[str, Any] = {
        "formula_covariates": [
            "age_at_admit",
            "gender",
            "admission_type",
            "first_careunit",
            "anchor_year_group",
        ],
        "cov_type": "HC1",
        "n": n,
        "n_events": n_events,
    }
    if n == 0 or n_events in (0, n):
        payload |= {"status": "not_fit", "reason": "constant outcome"}
        return payload

    import warnings

    import numpy as np
    import statsmodels.formula.api as smf
    from sklearn.metrics import roc_auc_score

    pdf = frame.to_pandas()
    # Separation probe (real on every tier: a handful of rare first_careunit /
    # admission_type levels have zero deaths). A level with zero events or zero
    # non-events drives its ML coefficient to +-inf, so the specified model has no
    # finite HC1 CI for it — and as that coefficient diverges, the level's rows'
    # likelihood contribution goes to 1, so the profile likelihood of every OTHER
    # coefficient equals the likelihood with those rows removed. Excluding them fits
    # the estimable coefficients exactly; the degenerate levels are named in
    # model.json / the report instead of an unstable table. Iterated to a fixed point
    # (an exclusion can degenerate another level's margin). Level labels are public
    # vocabulary; level sizes are deliberately not recorded (they may be small).
    outcome = pdf["hospital_expire_flag"].to_numpy()
    separated: list[str] = []
    keep = np.ones(len(pdf), dtype=bool)
    while True:
        found = False
        for col in ("gender", "admission_type", "first_careunit", "anchor_year_group"):
            values = pdf[col].to_numpy()
            for level in np.unique(values[keep]):
                cell = keep & (values == level)
                events = int(outcome[cell].sum())
                if events in (0, int(cell.sum())):
                    kind = "zero-event" if events == 0 else "all-event"
                    separated.append(f"{col}={level} ({kind})"[:VALUE_MAX_CHARS])
                    keep &= values != level
                    found = True
        if not found:
            break
    n_fit = int(keep.sum())
    n_events_fit = int(outcome[keep].sum()) if n_fit else 0
    if separated:
        payload |= {"separated_levels": separated, "n_excluded": n - n_fit}
    payload |= {"n_fit": n_fit, "n_events_fit": n_events_fit}
    if n_fit == 0 or n_events_fit in (0, n_fit):
        payload |= {"status": "not_fit", "reason": "constant outcome after exclusions"}
        return payload
    pdf_fit = pdf.loc[keep]
    try:
        with warnings.catch_warnings():
            # non-convergence is detected and reported explicitly below; the warning
            # (and the exp overflow it causes) would only leak into job logs / CLI
            warnings.simplefilter("ignore")
            fitted = smf.logit(MODEL_FORMULA, data=pdf_fit).fit(disp=0, cov_type="HC1")
            conf = np.exp(fitted.conf_int())
            ors = np.exp(fitted.params)
            auc = float(roc_auc_score(pdf_fit["hospital_expire_flag"], fitted.predict(pdf_fit)))
    except Exception as exc:  # separation / singular matrix on tiny tiers
        reason = f"{type(exc).__name__}: {exc}"[:VALUE_MAX_CHARS]
        payload |= {"status": "not_fit", "reason": reason}
        return payload
    converged = bool(fitted.mle_retvals.get("converged", False))
    if not converged or not (np.isfinite(conf.to_numpy()).all() and np.isfinite(ors).all()):
        # possible on the tiny fixture (quasi-separation): NaN/inf CIs are not an OR table
        payload |= {"status": "not_fit", "reason": "logit did not converge (separation)"}
        return payload
    payload |= {
        "status": "fit",
        "auc_in_sample": round(auc, 4),
        "converged": converged,
        "terms": [
            {
                "term": _term_label(str(term)),
                "odds_ratio": round(float(ors[term]), 4),
                "ci_low": round(float(conf.loc[term, 0]), 4),
                "ci_high": round(float(conf.loc[term, 1]), 4),
            }
            for term in fitted.params.index
        ],
    }
    return payload


# ---------------------------------------------------------------------------
# Report + run folder
# ---------------------------------------------------------------------------


def _md_table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return lines


def render_report(
    *,
    run_id: str,
    tier: str,
    snapshot_id: str | None,
    att: list[dict[str, Any]],
    desc: dict[str, Any],
    model: dict[str, Any],
    manifest: dict[str, Any],
) -> str:
    """The ``report.md`` body (ASCII; integers via ``fmt_int``, guard G4)."""
    from mimicwarehouse.inventory import fmt_int

    k = desc["k"]

    def count_cell(n: Any) -> str:
        return f"suppressed (< {k})" if n is None else fmt_int(int(n))

    lines = [
        "# Tracer bullet: first ICU stay of adult patients -> in-hospital mortality",
        "",
        f"Run `{run_id}` - tier `{tier}` (EP-31).",
        "",
        "## Question",
        "",
        "Among adult patients, on their first ICU stay, what is the association between",
        "admission characteristics (age at admission, gender, admission type, first care",
        "unit, anchor-year era) and in-hospital mortality?",
        "",
        "## Data",
        "",
        f"- Tier catalog: `{tier}` - core snapshot `{snapshot_id or '-'}`.",
        f"- {RETROSPECTIVE_SENTENCE}",
        "- Outcome: in-hospital death (discharge alive is the competing state; the",
        "  post-discharge death date is not used).",
        "- Age at admission is derived from the anchor fields; ages >= 89 appear as 91,",
        "  so age is capped at 91.",
        "- The anchor-year era (3-year windows) is the only temporal axis and enters as a",
        "  covariate, never a calendar.",
        "",
        "## Cohort",
        "",
        "One row per criterion, counted through the audited safe-query wrapper:",
        "",
        *_md_table(
            ["step", "n"],
            [[row["step"], count_cell(row["n"])] for row in att],
        ),
        "",
        "## Descriptives",
        "",
        f"Row-wise k = {k} suppression applies to both count columns (a row with any",
        f"count in 1..{k - 1} is dropped); suppressed rows are counted below each table.",
        "",
        "### Mortality by age band x gender",
        "",
        *_md_table(
            ["age band", "gender", "n", "deaths"],
            [
                [str(r["age_band"]), str(r["gender"]), fmt_int(r["n"]), fmt_int(r["n_deaths"])]
                for r in desc["by_age_gender"]["rows"]
            ],
        ),
        "",
        f"Suppressed rows: {fmt_int(desc['by_age_gender']['rows_suppressed'])}.",
        "",
        "### Mortality by first care unit",
        "",
        *_md_table(
            ["first care unit", "n", "deaths"],
            [
                [str(r["first_careunit"]), fmt_int(r["n"]), fmt_int(r["n_deaths"])]
                for r in desc["by_first_careunit"]["rows"]
            ],
        ),
        "",
        f"Suppressed rows: {fmt_int(desc['by_first_careunit']['rows_suppressed'])}.",
        "",
        "## Model",
        "",
        f"**Claim type: {CLAIM_TYPE}.**",
        "",
    ]
    if model["status"] == "fit":
        lines += [
            "Logistic regression (treatment coding, HC1 robust errors);",
            f"n = {fmt_int(model['n_fit'])}, events = {fmt_int(model['n_events_fit'])};",
            f"AUC = {model['auc_in_sample']:.3f} (in-sample, optimistic - not a",
            "prediction claim).",
        ]
        if model.get("separated_levels"):
            excluded = model["n_excluded"]
            excluded_text = fmt_int(excluded) if excluded == 0 or excluded >= k else f"< {k}"
            lines += [
                "",
                f"{fmt_int(len(model['separated_levels']))} rare covariate level(s) had a",
                "zero cell (no events, or no non-events) and are inestimable by maximum",
                "likelihood; their rows are excluded, which fits the remaining",
                f"coefficients exactly (excluded rows: {excluded_text}):",
                "",
                *(f"- {level}" for level in model["separated_levels"]),
            ]
        lines += [
            "",
            *_md_table(
                ["term", "OR", "95% CI"],
                [
                    [
                        t["term"],
                        f"{t['odds_ratio']:.3f}",
                        f"{t['ci_low']:.3f} - {t['ci_high']:.3f}",
                    ]
                    for t in model["terms"]
                    if t["term"] != "Intercept"
                ],
            ),
        ]
    else:
        lines += [
            f"Model: not_fit ({model['reason']}).",
            f"n = {fmt_int(model['n'])}, events = {fmt_int(model['n_events'])}.",
        ]
        if model.get("separated_levels"):
            lines += [
                "",
                "Degenerate covariate levels (a zero cell drives the ML coefficient to",
                "+-infinity, so the specified model has no finite confidence intervals):",
                "",
                *(f"- {level}" for level in model["separated_levels"]),
            ]
    lines += [
        "",
        "## What this deliberately does not claim",
        "",
        "- No causal effect: covariates are admission characteristics, not interventions.",
        "- No prediction performance: the AUC is in-sample and optimistic (holdout",
        "  modelling is P7).",
        "- No calendar-time trends: the era covariate orders 3-year windows, not years.",
        "- Ages >= 89 are indistinguishable (all appear as 91).",
        "- ICU length of stay is post-index and is not a covariate.",
        "",
        "## Reproduction",
        "",
        f"    mwh tracer --tier {tier}",
        "",
        f"Run id `{run_id}`; every displayed number came through the audited",
        "safe-query wrapper (audit ids in `manifest.json`).",
        "",
        "## Provenance",
        "",
        f"- git `{manifest['git_sha']}` - package `{manifest['package_version']}` -",
        f"  DuckDB `{manifest['duckdb_version']}`.",
        f"- Core snapshot `{snapshot_id or '-'}`; "
        f"{fmt_int(len(manifest['audit_ids']))} audited safe-query calls.",
        f"- Wall time {manifest['wall_s']} s.",
        "",
    ]
    return "\n".join(lines)


def runs_tracer_dir(settings: Settings | None = None) -> Path:
    """``<data_root>/runs/tracer`` — where run folders land."""
    return (settings or get_settings()).layout["runs"] / "tracer"


def _new_run_dir(tier: str, settings: Settings) -> tuple[str, Path]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    parent = runs_tracer_dir(settings)
    for attempt in range(100):
        run_id = f"{stamp}-{tier}" if attempt == 0 else f"{stamp}-{tier}-{attempt + 1}"
        out = parent / run_id
        if not out.exists():
            out.mkdir(parents=True)
            return run_id, out
    raise TracerError(f"cannot allocate a run folder under {parent}")


def _write_json(path: Path, payload: Any) -> None:
    from mimicwarehouse.inventory import _atomic_write_text

    _atomic_write_text(path, json.dumps(payload, indent=2, default=str) + "\n")


def run_tracer(
    tier: Tier | str, *, out: Path | None = None, settings: Settings | None = None
) -> TracerResult:
    """One full tracer run (module docstring): attrition + descriptives via
    ``safe_query``, the in-process model, five files under the run folder."""
    import duckdb

    from mimicwarehouse import __version__
    from mimicwarehouse.dag.runner import git_short_sha
    from mimicwarehouse.safe import K_FLOOR

    settings = settings or get_settings()
    started = time.perf_counter()
    att = attrition(tier, settings=settings)
    desc = descriptives(tier, settings=settings)
    model = fit(tier, settings=settings)

    if out is not None:
        out_dir = Path(out)
        out_dir.mkdir(parents=True, exist_ok=True)
        run_id = out_dir.name
    else:
        run_id, out_dir = _new_run_dir(str(tier), settings)

    audit_ids = [row["audit_id"] for row in att] + [
        desc[name]["audit_id"] for name in ("by_age_gender", "by_first_careunit")
    ]
    snapshot_id = _snapshot_of_last_call(settings, audit_ids)
    cohort_n = att[-1]["n"]
    manifest = {
        "run_id": run_id,
        "tier": str(tier),
        "git_sha": git_short_sha(),
        "package_version": __version__,
        "duckdb_version": duckdb.__version__,
        "core_snapshot_id": snapshot_id,
        "params": {"k": K_FLOOR, "adult_age_min": 18, "age_cap": 91, "actor": ACTOR},
        "cohort_n": cohort_n,
        "wall_s": round(time.perf_counter() - started, 2),
        "audit_ids": audit_ids,
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    report = render_report(
        run_id=run_id,
        tier=str(tier),
        snapshot_id=snapshot_id,
        att=att,
        desc=desc,
        model=model,
        manifest=manifest,
    )
    _write_json(out_dir / "manifest.json", manifest)
    _write_json(out_dir / "attrition.json", {"steps": att})
    _write_json(out_dir / "descriptives.json", desc)
    _write_json(out_dir / "model.json", model)
    from mimicwarehouse.inventory import _atomic_write_text

    _atomic_write_text(out_dir / "report.md", report)
    return TracerResult(
        run_id=run_id,
        tier=str(tier),
        out_dir=out_dir,
        attrition=att,
        descriptives=desc,
        model=model,
        manifest=manifest,
    )


def _snapshot_of_last_call(settings: Settings, audit_ids: list[str]) -> str | None:
    """The queried catalog's core snapshot id, read back from the audit log (the runs
    are all against one catalog; the last line is the cheapest authoritative source)."""
    from mimicwarehouse.safe import audit_path

    path = audit_path(settings)
    if not audit_ids or not path.is_file():
        return None
    wanted = set(audit_ids)
    snapshot: str | None = None
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("audit_id") in wanted:
                snapshot = (record.get("snapshot_ids") or {}).get("core") or snapshot
    return snapshot


# ---------------------------------------------------------------------------
# CLI (attached in cli.py)
# ---------------------------------------------------------------------------


def tracer_command(
    ctx: typer.Context,
    tier: Annotated[
        str,
        typer.Option("--tier", help="Tier catalog to run against: fixture | demo | dev | full."),
    ],
    background: Annotated[
        bool,
        typer.Option(
            "--background",
            help="Detach: launch this run as a background job (requires --job) and return.",
        ),
    ] = False,
    job: Annotated[
        str | None,
        typer.Option("--job", help="Job name for --background (state under runs/jobs/)."),
    ] = None,
) -> None:
    """Run the EP-31 tracer bullet (first-ICU-stay adults -> in-hospital mortality):
    attrition + descriptives via safe_query, a plain logistic regression, and a
    Markdown report under runs/tracer/ (aggregates only; audited)."""
    prefix = "mwh tracer"
    state: CliState = ctx.obj
    if tier not in TIERS:
        console.print(
            f"[bold red]{prefix}:[/] unknown tier {escape(tier)!s}; expected one of "
            f"{', '.join(TIERS)}",
            highlight=False,
        )
        raise typer.Exit(code=2)
    settings = state.settings

    if background:
        if not job:
            console.print(
                f"[bold red]{prefix}:[/] --background requires --job NAME", highlight=False
            )
            raise typer.Exit(code=2)
        from mimicwarehouse.dag import jobs as jobs_mod

        argv: list[str] = []
        if state.data_root_override is not None:
            argv += ["--data-root", str(state.data_root_override)]
        argv += ["tracer", "--tier", tier]
        try:
            info = jobs_mod.launch(argv, job, settings)
        except jobs_mod.JobError as exc:
            console.print(f"[bold red]{prefix}:[/] {escape(str(exc))}", highlight=False)
            raise typer.Exit(code=2) from None
        console.print(
            f"launched job [bold]{escape(info.job)}[/] (pid {info.pid}) - log {escape(info.log)}",
            highlight=False,
        )
        console.print(f"check it with: mwh jobs --job {escape(info.job)}", highlight=False)
        return

    from mimicwarehouse.catalog.connect import CatalogOpenError
    from mimicwarehouse.inventory import fmt_int
    from mimicwarehouse.safe import SafeQueryRefused

    try:
        result = run_tracer(tier, settings=settings)
    except SafeQueryRefused as exc:
        console.print(f"[bold red]{prefix}: refused:[/] {escape(str(exc))}", highlight=False)
        raise typer.Exit(code=3) from None
    except CatalogOpenError as exc:
        console.print(f"[bold red]{prefix}:[/] {escape(str(exc))}", highlight=False)
        raise typer.Exit(code=2) from None

    k = result.descriptives["k"]
    cohort_n = result.manifest["cohort_n"]
    cohort_text = f"suppressed (< {k})" if cohort_n is None else fmt_int(cohort_n)
    model = result.model
    model_text = (
        f"fit (AUC {model['auc_in_sample']:.3f} in-sample)"
        if model["status"] == "fit"
        else f"not_fit ({model['reason']})"
    )
    console.print(f"run [bold]{escape(result.run_id)}[/]  tier {result.tier}", highlight=False)
    console.print(f"cohort n = {cohort_text}  model: {escape(model_text)}", highlight=False)
    console.print(
        f"audited safe-query calls: {fmt_int(len(result.manifest['audit_ids']))}"
        f"  wall {result.manifest['wall_s']} s",
        highlight=False,
    )
    console.print(f"wrote {escape(str(result.out_dir))}", highlight=False)


__all__ = [
    "ACTOR",
    "AGE_BANDS",
    "CLAIM_TYPE",
    "MODEL_FORMULA",
    "RETROSPECTIVE_SENTENCE",
    "SQL_FILENAME",
    "STEPS",
    "VALUE_MAX_CHARS",
    "TracerError",
    "TracerResult",
    "attrition",
    "cohort_cte_sql",
    "descriptives",
    "fit",
    "render_report",
    "run_tracer",
    "runs_tracer_dir",
    "statement",
    "tracer_command",
]
