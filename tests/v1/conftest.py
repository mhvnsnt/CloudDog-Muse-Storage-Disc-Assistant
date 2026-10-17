"""v1 engine suite (src/clouddog) — import isolation.

The repo also contains a root-level ``clouddog/`` package (v3, sibling
lane) and ``tests/conftest.py`` puts the repo root on ``sys.path``. This
conftest runs after it (parent-first ordering) and re-points the
``clouddog`` import at ``src/clouddog`` for this subtree only, so the two
same-named packages can both be tested.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_SRC = os.path.join(_REPO, "src")

# Drop any already-imported clouddog (the root v3 package) so the v1 tree wins.
for _mod in [m for m in sys.modules if m == "clouddog" or m.startswith("clouddog.")]:
    del sys.modules[_mod]
if _SRC in sys.path:
    sys.path.remove(_SRC)
sys.path.insert(0, _SRC)
