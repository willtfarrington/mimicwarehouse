"""The disclosure-gate fixtures (EP-43): three hand-typed, synthetic artefacts under
``tests/fixtures/disclose/`` that ``tests/ep/test_ep43.py`` and the EP-43 acceptance
commands exercise. Written by ``mwh fixtures disclose`` (byte-identical on every run —
``test_ep43`` asserts the committed copies equal :data:`FILES`), never by hand: the
session deny rules refuse the Write tool on ``*.csv`` paths, so the generator is the one
sanctioned writer of a committed CSV, exactly as ``mwh fixtures build`` is for the
hosp / icu tree (D-42, EP-170 amendment 1).

Content rules: identifier-*named* columns carry fixture-band ids (>= 90 000 000) so the
checker's ``ID_COL`` rule fires while guard G4 stays clean; no pragma anywhere; ASCII;
nothing derived from data.
"""

from __future__ import annotations

from pathlib import Path

#: Sub-directory under ``tests/fixtures``.
FIXTURE_SUBDIR = "disclose"

#: file name -> content (LF line ends, trailing newline).
FILES: dict[str, str] = {
    # ID_COL: the column *name* is the violation; the ids are fixture-band values
    "bad_ids.csv": "subject_id,n\n90000001,15\n90000002,20\n90000003,25\n",
    # SMALL_CELL: an unmarked count of 7 in a Markdown table
    "bad_small_cell.md": (
        "# Crafted small cell (synthetic; EP-43 fixture)\n"
        "\n"
        "A hand-written aggregate table whose second row carries a count below the small-cell\n"
        "threshold without a suppression marker. `mwh disclose check` must refuse it with\n"
        "`SMALL_CELL`. Nothing here is derived from data.\n"
        "\n"
        "| age band | n | deaths |\n"
        "|---|---:|---:|\n"
        "| 18-39 | 120 | 12 |\n"
        "| 40-64 | 7 | 0 |\n"
    ),
    # clean: every count >= 11 and every nested difference (n - n_deaths) >= 11
    "good_aggregate.csv": (
        "anchor_year_group,gender,n,n_deaths\n"
        "2008 - 2010,F,120,12\n"
        "2008 - 2010,M,130,14\n"
        "2011 - 2013,F,140,15\n"
        "2011 - 2013,M,150,16\n"
    ),
}


def default_disclose_dir() -> Path:
    """``<workspace>/tests/fixtures/disclose``."""
    from mimicwarehouse.fixtures.write import default_out_dir

    return default_out_dir() / FIXTURE_SUBDIR


def write_disclose_fixtures(out_dir: Path | None = None) -> list[Path]:
    """Write :data:`FILES` under ``out_dir`` (default :func:`default_disclose_dir`);
    returns the paths written, in name order."""
    target = Path(out_dir) if out_dir is not None else default_disclose_dir()
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in sorted(FILES):
        path = target / name
        path.write_bytes(FILES[name].encode("ascii"))
        written.append(path)
    return written


def drift(out_dir: Path | None = None) -> list[str]:
    """Names under ``out_dir`` whose bytes differ from :data:`FILES` (or are missing)."""
    target = Path(out_dir) if out_dir is not None else default_disclose_dir()
    return [
        name
        for name, content in sorted(FILES.items())
        if not (target / name).is_file() or (target / name).read_bytes() != content.encode("ascii")
    ]


__all__ = ["FILES", "FIXTURE_SUBDIR", "default_disclose_dir", "drift", "write_disclose_fixtures"]
