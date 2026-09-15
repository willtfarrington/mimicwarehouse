"""``python -m mimicwarehouse.qc`` — re-render ``dag/specs/qc.yaml`` from the contract +
thresholds and the generated blocks of ``docs/methods/qc.md`` (EP-44)."""

from __future__ import annotations

from mimicwarehouse.qc.profile import sync_spec
from mimicwarehouse.qc.report import sync_methods_doc

if __name__ == "__main__":
    print(sync_spec())
    print(sync_methods_doc())
