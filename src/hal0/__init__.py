"""hal0 — open-source home AI inference platform."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("hal0")
except PackageNotFoundError:
    # Importing the source tree without `pip install`-ing it (e.g. a
    # `python -c "import hal0"` from a repo clone) bypasses metadata.
    # Surface a clear sentinel rather than a confusing crash.
    __version__ = "0.0.0+source"
