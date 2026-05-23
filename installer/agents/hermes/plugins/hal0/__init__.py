"""hal0 model-provider profile — STUB for #240.

Lives at ``$HERMES_HOME/plugins/model-providers/hal0/`` after bootstrap
copies the package data. The real :class:`Hal0Profile` implementation
(subclass of ``providers.base.ProviderProfile``) lands in #241; this
stub exists so the install phase (#240) can copy a non-empty file into
position and the directory survives ``pip install``-style packaging
quirks.
"""

# Intentionally empty — see #241.
