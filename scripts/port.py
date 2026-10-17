"""Stable per-worktree API port, independent of Python's salted hash."""

import argparse
import hashlib
from pathlib import Path


def worktree_port(path: Path | str = ".") -> int:
    digest = hashlib.sha256(str(Path(path).resolve()).encode()).digest()
    return 8100 + int.from_bytes(digest[:4], "big") % 100


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Порт API текущего worktree")
    parser.add_argument("path", nargs="?", default=".")
    print(worktree_port(parser.parse_args().path))
