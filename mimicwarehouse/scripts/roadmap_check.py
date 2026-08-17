"""Thin entry point: ``uv run poe roadmap-check [--strict] [--json] [--roadmap DIR]``.

Same check as ``mwh verify --roadmap`` (:func:`mimicwarehouse.verify.roadmap_check`, EP-6):
parses ``roadmap/README.md`` and every ``roadmap/EP-*.md`` brief and reports parity, header,
☑-hash and charter findings. It never edits the roadmap. Exit 0 ok / 1 errors (or warnings
with ``--strict``) / 2 usage.
"""

from __future__ import annotations

import sys

from mimicwarehouse.verify import roadmap_check_main

if __name__ == "__main__":
    sys.exit(roadmap_check_main())
