"""Verify a published handoff ZIP without extraction or ML dependencies."""

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import zipfile


def verify(path: Path, expected_sha256: str) -> dict[str, int | str]:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != expected_sha256.lower():
        raise ValueError("Archive SHA-256 mismatch")
    root = "Forte_Dataset_Handoff_20261017"
    checksum_name = f"{root}/CHECKSUMS.sha256"
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive entries")
        for name in names:
            parts = PurePosixPath(name)
            if parts.is_absolute() or ".." in parts.parts or not name.startswith(root + "/"):
                raise ValueError(f"Unexpected archive path: {name}")
        manifest = {}
        for line in archive.read(checksum_name).decode("utf-8").splitlines():
            expected, relative = line.split("  ", 1)
            name = f"{root}/{relative}"
            if name in manifest:
                raise ValueError("Duplicate checksum entry")
            manifest[name] = expected
        if set(names) != set(manifest) | {checksum_name}:
            raise ValueError("Manifest does not cover the complete archive")
        for name, expected in manifest.items():
            with archive.open(name) as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise ValueError(f"Entry SHA-256 mismatch: {name}")
        for launcher in ("START_TRAINING.command", "CHECK_ONLY.command"):
            entry = archive.getinfo(f"{root}/Forte_M2Pro_Reviewed_v2/{launcher}")
            if not (entry.external_attr >> 16) & 0o111:
                raise ValueError(f"Missing executable permission: {launcher}")
    return {"sha256": digest, "files": len(names), "status": "PASS"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    print(verify(args.archive, args.sha256))
