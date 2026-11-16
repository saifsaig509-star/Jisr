"""Tiny JSON settings file: %APPDATA%\\BTBridge\\config.json (PIN, gamepad mode, ...)."""
import json
import os
from pathlib import Path


def config_path() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home())
    return base / "BTBridge" / "config.json"


def load() -> dict:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def update(**changes) -> dict:
    """Merge `changes` into the saved settings (other keys are kept)."""
    data = load()
    data.update(changes)
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass            # settings are a convenience; never crash because of them
    return data
