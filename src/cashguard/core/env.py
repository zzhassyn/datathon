"""Offline model processes; no model imports are needed here."""

import os
from collections.abc import MutableMapping

OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "YOLO_OFFLINE": "1",
    "YOLO_AUTOINSTALL": "false",
}


def apply_offline_env(environ: MutableMapping[str, str] | None = None) -> None:
    (os.environ if environ is None else environ).update(OFFLINE_ENV)
