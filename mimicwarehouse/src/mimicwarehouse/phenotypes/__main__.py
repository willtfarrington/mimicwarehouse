"""``python -m mimicwarehouse.phenotypes`` — re-render the generated block of
``docs/methods/phenotypes.md`` (EP-41)."""

from __future__ import annotations

from mimicwarehouse.phenotypes.runner import sync_methods_doc

if __name__ == "__main__":
    print(sync_methods_doc())
