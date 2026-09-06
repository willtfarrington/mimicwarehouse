"""``python -m mimicwarehouse.codesets`` — re-render the generated block of
``docs/methods/codesets.md`` (EP-40)."""

from __future__ import annotations

from mimicwarehouse.codesets.registry import sync_methods_doc

if __name__ == "__main__":
    print(sync_methods_doc())
