"""mimicwarehouse — a local MIMIC-IV data lab.

DuckDB + Parquet warehouse, Python backend and Streamlit "Lab" app over MIMIC-IV 3.1
(hosp + icu), MIMIC-IV-ED 2.2 and MIMIC-IV-Note 2.2. Read ``GOVERNANCE.md`` before
touching data: every query from an agent session goes through
:mod:`mimicwarehouse.safe` (EP-30) and returns aggregates only.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mimicwarehouse")
except PackageNotFoundError:  # pragma: no cover - source checkout without an install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
