"""``python -m mimicwarehouse.concepts.patches`` — refresh + validate the registry (EP-38)."""

from __future__ import annotations

import sys

from mimicwarehouse.concepts.patches import main

if __name__ == "__main__":
    sys.exit(main())
