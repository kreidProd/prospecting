import json
from pathlib import Path
from threading import Lock

DEFAULTS = {
    "outscraper_api_key": "",
    "apify_api_token": "",
    "hunter_api_key": "",
    "neverbounce_api_key": "",
    "clickup_api_key": "",
    "clickup_list_id": "",
    "default_radius_miles": 25,
    "default_limit": 500,
    "fetch_timeout_seconds": 10,
    "pipeline_workers": 20,
    "business_name": "Reboot",
    "user_name": "",
}

SECRET_FIELDS = {
    "outscraper_api_key",
    "apify_api_token",
    "hunter_api_key",
    "neverbounce_api_key",
    "clickup_api_key",
}


class SettingsStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = Lock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(DEFAULTS, indent=2))

    def read_raw(self) -> dict:
        with self.lock:
            try:
                data = json.loads(self.path.read_text() or "{}")
            except json.JSONDecodeError:
                data = {}
        merged = {**DEFAULTS, **data}
        return merged

    def read_public(self) -> dict:
        """Returns settings with secret fields masked."""
        data = self.read_raw()
        out = {}
        for k, v in data.items():
            if k in SECRET_FIELDS and v:
                out[k] = "••••" + (v[-4:] if len(v) >= 4 else "")
                out[f"{k}_set"] = True
            elif k in SECRET_FIELDS:
                out[k] = ""
                out[f"{k}_set"] = False
            else:
                out[k] = v
        return out

    def update(self, patch: dict) -> dict:
        with self.lock:
            try:
                current = json.loads(self.path.read_text() or "{}")
            except json.JSONDecodeError:
                current = {}
            merged = {**DEFAULTS, **current}
            for k, v in patch.items():
                if k not in DEFAULTS:
                    continue
                # Skip empty values for secret fields so mask round-trips don't wipe keys
                if k in SECRET_FIELDS and (v is None or v == "" or str(v).startswith("••••")):
                    continue
                merged[k] = v
            self.path.write_text(json.dumps(merged, indent=2))
        return self.read_public()

    def get(self, key: str):
        return self.read_raw().get(key, DEFAULTS.get(key))
