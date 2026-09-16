"""``python -m mimicwarehouse.cohort`` — re-render the generated blocks of
``docs/methods/cohorts.md`` (EP-46)."""

from __future__ import annotations

from mimicwarehouse.cohort.registry import sync_methods_doc

if __name__ == "__main__":
    print(sync_methods_doc())
