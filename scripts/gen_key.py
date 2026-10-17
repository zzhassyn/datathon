"""Generate local API credentials without displaying secret material."""

import argparse
import os
import secrets
import tempfile
from pathlib import Path


def generate_keys(path: Path, *, rotate: bool = False) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    original = path.read_text() if path.exists() else ""
    lines = original.splitlines()
    exists = any(line.strip().startswith("CG_API_KEYS=") for line in lines)
    if exists and not rotate:
        raise FileExistsError("Ключи уже существуют; для замены нужен --rotate")
    value = (
        f"CG_API_KEYS=jury:{secrets.token_urlsafe(32)},team:{secrets.token_urlsafe(32)}"
    )
    lines = [line for line in lines if not line.strip().startswith("CG_API_KEYS=")]
    lines.append(value)
    fd, tmp_name = tempfile.mkstemp(prefix=".env-", dir=path.parent)
    try:
        os.chmod(tmp_name, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Сохранить API-ключи локально без вывода в терминал"
    )
    parser.add_argument("--path", type=Path, default=Path("~/.config/cashguard/.env"))
    parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args()
    try:
        generate_keys(args.path, rotate=args.rotate)
    except FileExistsError as error:
        parser.exit(1, f"{error}\n")
    print(f"Ключи сохранены: {args.path.expanduser()} (0600)")
