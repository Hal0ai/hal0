"""Regenerate the Settings fixtures the screenshot showcase serves.

The Settings pages read GET /api/settings, /api/settings/fields,
/api/settings/schema and /api/settings/apply-plan. Rather than hand-write
those bodies, this renders them from hal0's own schema with every value at
its default, exactly as the routes in src/hal0/api/routes/settings.py do for
a box with no hal0.toml. Nothing is read from a real box.

Run from the repo root:

    python ui/scripts/screenshots/gen_settings_data.py

hal0's path resolver honours HAL0_HOME, so the run points it at a throwaway
directory (importing the API package touches state paths) and then maps that
prefix back to the FHS paths a real install uses.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "showcase" / "data"

FHS = {
    "/usr-lib/": "/usr/lib/hal0/",
    "/etc/": "/etc/hal0/",
    "/var-lib/": "/var/lib/",
    "/var-log/": "/var/log/",
}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="hal0-shots-") as home:
        os.environ["HAL0_HOME"] = home
        sys.path.insert(0, str(REPO / "src"))

        from hal0.api._settings_fields import build_settings_fields
        from hal0.api.routes import settings as routes
        from hal0.config.schema import Hal0Config

        cfg = Hal0Config()
        fields = build_settings_fields(cfg)
        for row in fields:
            row["live_target"] = None
        bodies = {
            "settings-defaults.json": routes._config_to_dict(cfg),
            "settings-fields.json": {"fields": fields},
            "settings-schema.json": Hal0Config.model_json_schema(),
            "settings-apply-plan.json": asyncio.run(routes.get_apply_plan()),
        }

        OUT.mkdir(parents=True, exist_ok=True)
        for name, body in bodies.items():
            text = json.dumps(body, indent=1, sort_keys=True, default=str)
            for dev, fhs in FHS.items():
                text = text.replace(home + dev, fhs)
            if home in text:
                raise SystemExit(f"{name}: the temporary HAL0_HOME leaked into the output")
            (OUT / name).write_text(text + "\n", encoding="utf-8")
            print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
