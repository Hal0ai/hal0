"""hal0 — open-source home AI inference platform."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Distribution name is "hal0ai" (PyPI rejects "hal0"); the fallback to
    # "hal0" covers a transitional editable install whose .dist-info still
    # carries the old name until it's re-`pip install`-ed.
    try:
        __version__ = _pkg_version("hal0ai")
    except PackageNotFoundError:
        __version__ = _pkg_version("hal0")
except PackageNotFoundError:
    # Importing the source tree without `pip install`-ing it (e.g. a
    # `python -c "import hal0"` from a repo clone) bypasses metadata.
    # Surface a clear sentinel rather than a confusing crash.
    __version__ = "0.0.0+source"
