"""``python -m mimicwarehouse.protocol`` — re-render the generated blocks of
``docs/methods/protocols.md`` (EP-51)."""

from __future__ import annotations

from mimicwarehouse.protocol.registry import sync_methods_doc

if __name__ == "__main__":
    print(sync_methods_doc())
