"""Shared transport primitives for hal0 Hermes adapters."""

from .client import Hal0HermesClient
from .errors import IncompatibleSchema, MissingResource, Unauthorized, Unavailable

__all__ = [
    "Hal0HermesClient",
    "IncompatibleSchema",
    "MissingResource",
    "Unauthorized",
    "Unavailable",
]
