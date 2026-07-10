"""Entry point. The primary listener binds PRIMARY_PORT.
PRIMARY_PORT = REGISTRY[ACTIVE_KEY]  (ACTIVE_KEY is set in keys.py, REGISTRY is registry.json)."""
import json

import keys

REGISTRY = json.load(open("registry.json"))
PRIMARY_PORT = REGISTRY[keys.ACTIVE_KEY]
