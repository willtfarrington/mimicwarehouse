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
  is recorded). (The brief's ``sum(hospital_expire_flag)`` returns HUGEINT; when EP-31
  shipped, the safe-query walk refused a re-cast, so the FILTER form was chosen — it is
  also the better statement, a real count the suppressor recognises. EP-33 B1a lifted the
  cast restriction — ``CAST(sum(x) AS BIGINT)`` now verifies — but the shipped FILTER
  statements stay as they are.)
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
  Since EP-35 the whole run is wrapped in :func:`mimicwarehouse.run.start` (``name``
  ``tracer``, ``kind`` ``analysis``): the provenance run ledger records every statement
  the tracer issued (``sql/attrition_<step>.sql``, ``sql/by_age_gender.sql``,
  ``sql/by_first_careunit.sql``, ``sql/model_frame.sql``), the attrition table, the
  ``core`` snapshot id, the audit ids and the claim type, and the tracer manifest cites the
  run under ``ledger_run_id``. The tracer's own folder and every number are unchanged
  (the EP-35 acceptance): the report artefacts stay under ``runs/tracer/``, the formal run
  record lives under ``runs/<run_id>/``.

Nothing under the run folder may carry an identifier column name or a value longer than
64 characters — model term labels are normalised to ``<var>=<level>`` and truncated.
Time semantics cite :mod:`mimicwarehouse.timesem` since EP-34: the cohort SQL embeds
``timesem.sql_age_at`` fragments verbatim (the age rule, ``AGE_CAP`` = 91), the
descriptives band ages with ``timesem.sql_age_band`` over :data:`AGE_BANDS` (now defined
there and re-exported here), and ``anchor_year_group`` enters the model as the era
covariate exactly as before — the EP-34 acceptance was count-for-count identity of every
tracer number on dev.

CLI: ``mwh tracer --tier {fixture,demo,dev,full} [--background --job NAME]`` (attached in
:mod:`mimicwarehouse.cli`; ``--background`` reuses :func:`mimicwarehouse.dag.jobs.launch`).
Import budget: duckdb / polars / pandas / statsmodels and :mod:`mimicwarehouse.run` are
imported inside the function bodies (cli.py rule).
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
from mimicwarehouse.console import EXIT_USAGE, console, fail
from mimicwarehouse.timesem import AGE_BANDS, AGE_CAP, sql_age_band

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cli import CliState

#: Audit actor of every tracer safe_query call (the acceptance counts these lines).
ACTOR = "tracer"

#: CTE steps of ``sql/tracer_first_icu_mortality.sql``, in attrition order.
STEPS: tuple[str, ...] = ("base", "first_stay", "adult", "complete", "cohort")

#: Human labels of the steps for the EP-35 attrition record (one row per criterion).
STEP_LABELS: dict[str, str] = {
    "base": "ICU stays joined to their admission and patient",
    "first_stay": "first ICU stay per subject",
    "adult": "age at admission >= 18",
    "complete": "complete covariates",
    "cohort": "analysis cohort",
}

#: The covariate frame the model reads in-process (recorded as ``sql/model_frame.sql``).
MODEL_FRAME_SELECT = (
    "SELECT age_at_admit, gender, admission_type, first_careunit, "
    "anchor_year_group, hospital_expire_flag FROM cohort"
)

# AGE_BANDS (the descriptives' bands: upper bounds exclusive, cohort floor 18) lives in
# ``timesem`` since EP-34 and is re-exported here for the EP-31 surface.

#: The model formula (treatment coding via C(); age stays linear).
MODEL_FORMULA = (
    "hospital_expire_flag ~ age_at_admit + C(gender) + C(admission_type) "
    "+ C(first_careunit) + C(anchor_year_group)"
)

#: Longest string value allowed in any run-folder file. Deliberately a **mirror** of
#: ``safe.FREE_TEXT_MAX_CHARS`` rather than an import: this module is imported by
#: ``cli.py`` at start-up and ``safe`` is not (``mwh --help`` import budget, DESIGN §15);
#: ``test_ep33`` asserts the two stay equal.
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


def descriptive_statements() -> dict[str, str]:
    """The two descriptive statements by table name (what :func:`descriptives` runs and
    what the EP-35 run record stores under ``sql/``)."""
    counts = "count(*) AS n, count(*) FILTER (WHERE hospital_expire_flag = 1) AS n_deaths"
    return {
        "by_age_gender": statement(
            f"SELECT {sql_age_band('age_at_admit')} AS age_band, gender, {counts} "
            "FROM cohort GROUP BY 1, 2 ORDER BY 1, 2"
        ),
        "by_first_careunit": statement(
            f"SELECT first_careunit, {counts} FROM cohort GROUP BY 1 ORDER BY 1"
        ),
    }


def descriptives(tier: Tier | str, *, settings: Settings | None = None) -> dict[str, Any]:
    """Suppressed mortality tables (module docstring): ``by_age_gender`` and
    ``by_first_careunit``, each ``{rows, rows_suppressed, audit_id}``; plus ``k``."""
    from mimicwarehouse.safe import K_FLOOR, safe_query

    settings = settings or get_settings()
    queries = descriptive_statements()
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
        frame = con.execute(statement(MODEL_FRAME_SELECT)).pl()
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
        from mimicwarehouse.safe import sanitize_error_text

        # DKB-2: no raw error text (it could quote a value) enters a run-folder file
        reason = f"{type(exc).__name__}: {sanitize_error_text(str(exc))}"[:VALUE_MAX_CHARS]
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
    from mimicwarehouse.fsio import atomic_write_text

    atomic_write_text(path, json.dumps(payload, indent=2, default=str) + "\n")


def run_tracer(
    tier: Tier | str, *, out: Path | None = None, settings: Settings | None = None
) -> TracerResult:
    """One full tracer run (module docstring): attrition + descriptives via
    ``safe_query``, the in-process model, five files under the run folder — inside an
    EP-35 provenance run (``run.start``) that records the statements, the attrition, the
    snapshot id and the audit ids without changing a number."""
    import duckdb

    from mimicwarehouse import __version__
    from mimicwarehouse import run as run_mod
    from mimicwarehouse.dag.runner import git_short_sha
    from mimicwarehouse.safe import K_FLOOR

    settings = settings or get_settings()
    params = {"k": K_FLOOR, "adult_age_min": 18, "age_cap": AGE_CAP, "actor": ACTOR}
    with run_mod.start(
        "tracer",
        tier=str(tier),
        kind="analysis",
        params=params,
        settings=settings,
        command=f"mwh tracer --tier {tier}",
        claim_type=CLAIM_TYPE,
    ) as r:
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

        # EP-35: the formal run record — statements, attrition, snapshot id, audit ids
        for step in STEPS:
            r.record_sql(f"attrition_{step}", statement(f"SELECT count(*) AS n FROM {step}"))
        for name, sql in descriptive_statements().items():
            r.record_sql(name, sql)
        r.record_sql("model_frame", statement(MODEL_FRAME_SELECT))
        r.record_attrition(
            [
                {
                    "step": row["step"],
                    "label": STEP_LABELS[row["step"]],
                    "n_units": row["n"],
                    # one row per subject from first_stay on; base is one row per stay
                    "n_subjects": None if row["step"] == "base" else row["n"],
                }
                for row in att
            ]
        )
        if snapshot_id:
            r.record_snapshot("core", snapshot_id)
        r.record_audit(*audit_ids)

        manifest = {
            "run_id": run_id,
            "ledger_run_id": r.run_id,
            "tier": str(tier),
            "git_sha": git_short_sha(),
            "package_version": __version__,
            "duckdb_version": duckdb.__version__,
            "core_snapshot_id": snapshot_id,
            "params": params,
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
        from mimicwarehouse.fsio import atomic_write_text

        atomic_write_text(out_dir / "report.md", report)
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
    from mimicwarehouse.fsio import iter_jsonl
    from mimicwarehouse.safe import audit_path

    if not audit_ids:
        return None
    wanted = set(audit_ids)
    snapshot: str | None = None
    for record in iter_jsonl(audit_path(settings)):  # tolerates a torn trailing line (LGR-1)
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
        fail(prefix, f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")
    settings = state.settings

    if background:
        if not job:
            fail(prefix, "--background requires --job NAME")
        from mimicwarehouse.dag import jobs as jobs_mod

        argv: list[str] = []
        if state.data_root_override is not None:
            argv += ["--data-root", str(state.data_root_override)]
        argv += ["tracer", "--tier", tier]
        try:
            info = jobs_mod.launch(argv, job, settings)
        except jobs_mod.JobError as exc:
            fail(prefix, str(exc))
        console.print(
            f"launched job [bold]{escape(info.job)}[/] (pid {info.pid}) - log {escape(info.log)}",
            highlight=False,
        )
        console.print(f"check it with: mwh jobs --job {escape(info.job)}", highlight=False)
        return

    from mimicwarehouse.catalog.cli import safe_cli_errors
    from mimicwarehouse.inventory import fmt_int

    def run() -> TracerResult:
        # refusal -> EXIT_REFUSED (3); usage / environment / TracerError -> EXIT_USAGE (2);
        # nothing tracebacks to exit 1 (EP-33 B1d)
        with safe_cli_errors(prefix):
            try:
                return run_tracer(tier, settings=settings)
            except TracerError as exc:
                fail(prefix, str(exc), code=EXIT_USAGE)
        raise AssertionError  # unreachable: safe_cli_errors exits on every error

    result = run()

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
    "MODEL_FRAME_SELECT",
    "RETROSPECTIVE_SENTENCE",
    "SQL_FILENAME",
    "STEPS",
    "STEP_LABELS",
    "VALUE_MAX_CHARS",
    "TracerError",
    "TracerResult",
    "attrition",
    "cohort_cte_sql",
    "descriptive_statements",
    "descriptives",
    "fit",
    "render_report",
    "run_tracer",
    "runs_tracer_dir",
    "statement",
    "tracer_command",
]
