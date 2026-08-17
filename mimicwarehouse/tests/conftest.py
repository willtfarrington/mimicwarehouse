"""Shared pytest configuration for mimicwarehouse.

Registers the roadmap marker convention (``ep_<n>``: acceptance tests of roadmap brief
EP-<n>) so ``uv run poe test -m ep_1`` selects one brief's tests under
``--strict-markers``, a placeholder ``tier(name)`` marker (selection semantics arrive in
EP-12), and the Hypothesis profiles.
"""

from __future__ import annotations

import os

import pytest
from hypothesis import settings as hypothesis_settings

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

EP_MARKER_RANGE = range(0, 200)  # ep_0 … ep_199


def pytest_configure(config: pytest.Config) -> None:
    for n in EP_MARKER_RANGE:
        config.addinivalue_line("markers", f"ep_{n}: acceptance tests of roadmap brief EP-{n}")
    config.addinivalue_line(
        "markers",
        "tier(name): data tier a test needs — 'fixture' (synthetic, default), 'dev' "
        "(5 % of subjects) or 'full'; selection semantics arrive in EP-12",
    )


# ---------------------------------------------------------------------------
# Hypothesis profiles — chosen by HYPOTHESIS_PROFILE (default: "default")
# ---------------------------------------------------------------------------

hypothesis_settings.register_profile("default", deadline=None, max_examples=50)
hypothesis_settings.register_profile("ci", deadline=None, max_examples=200)
hypothesis_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
