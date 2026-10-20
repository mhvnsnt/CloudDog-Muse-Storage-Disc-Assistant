"""v1 engine suite (src/clouddog) — import isolation.

The repo also contains a root-level ``clouddog/`` package (v3, sibling
lane) and ``tests/conftest.py`` puts the repo root on ``sys.path``. This
conftest runs after it (parent-first ordering) and re-points the
``clouddog`` import at ``src/clouddog`` for this subtree only, so the two
same-named packages can both be tested.
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_SRC = os.path.join(_REPO, "src")

# Drop any already-imported clouddog (the root v3 package) so the v1 tree wins.
for _mod in [m for m in sys.modules if m == "clouddog" or m.startswith("clouddog.")]:
    del sys.modules[_mod]
if _SRC in sys.path:
    sys.path.remove(_SRC)
sys.path.insert(0, _SRC)


@pytest.fixture(autouse=True)
def _clouddog_state_isolation(tmp_path_factory, monkeypatch):
    """Hard isolation (deleter audit 2026-10-20): ALL CloudDog runtime
    state (trust, heartbeat, journal, stream config) resolves through
    $CLOUDEDOG_STATE_DIR. Point it at a per-test tmp dir so the suite
    NEVER touches the real ~/.clouddog — a test that reset the real
    trust.json would fake a revocation/promotion on the live daemon,
    and daemon ticks used to overwrite the production heartbeat.json.

    The state dir lives in a tmp_path_factory dir, NOT inside the
    test's own tmp_path: several tests scan tmp_path and count its
    entries, and a state dir in there would appear as a phantom entry.
    """
    state = tmp_path_factory.mktemp("clouddog-state")
    monkeypatch.setenv("CLOUDEDOG_STATE_DIR", str(state))
    monkeypatch.delenv("CLOUDEDOG_TRUST_PATH", raising=False)
    return state
