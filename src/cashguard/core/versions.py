"""Stable version and configuration identities; no model contents are opened."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def config_hash(config: Mapping[str, Any]) -> str:
    normalized = json.dumps(
        config,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(normalized).hexdigest()


def pipeline_version(git_sha: str, manifest: Mapping[str, Any]) -> str:
    return f"{git_sha}:{config_hash(manifest)[:16]}"
