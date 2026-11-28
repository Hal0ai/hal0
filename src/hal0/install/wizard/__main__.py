"""Entry for `python3 -I <this file>`: runs before hal0's environment exists."""

import importlib.util
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "hal0_wizard", _here / "__init__.py", submodule_search_locations=[str(_here)]
)
assert _spec is not None and _spec.loader is not None
_pkg = importlib.util.module_from_spec(_spec)
sys.modules["hal0_wizard"] = _pkg
_spec.loader.exec_module(_pkg)

from hal0_wizard.cli import main  # type: ignore[import-not-found]  # noqa: E402

raise SystemExit(main())
