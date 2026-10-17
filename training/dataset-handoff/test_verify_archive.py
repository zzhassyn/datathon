"""Integrity and path checks for the release handoff verifier."""

import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from verify_archive import verify


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "handoff.zip"

    def build(self, *, extra=None, wrong_entry_hash=False, executable=True):
        root = "Forte_Dataset_Handoff_20261017"
        rows = []
        with zipfile.ZipFile(self.path, "w") as archive:
            for launcher in ("START_TRAINING.command", "CHECK_ONLY.command"):
                name = f"Forte_M2Pro_Reviewed_v2/{launcher}"
                data = b"#!/bin/bash\nexit 0\n"
                info = zipfile.ZipInfo(f"{root}/{name}")
                info.create_system = 3
                info.external_attr = (0o100755 if executable else 0o100644) << 16
                archive.writestr(info, data)
                digest = "0" * 64 if wrong_entry_hash else hashlib.sha256(data).hexdigest()
                rows.append(f"{digest}  {name}")
            archive.writestr(f"{root}/CHECKSUMS.sha256", "\n".join(rows) + "\n")
            if extra:
                archive.writestr(extra, b"unlisted")
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_valid_complete_archive(self):
        digest = self.build()
        self.assertEqual(verify(self.path, digest)["status"], "PASS")

    def test_wrong_archive_hash(self):
        self.build()
        with self.assertRaisesRegex(ValueError, "Archive SHA-256"):
            verify(self.path, "0" * 64)

    def test_wrong_entry_hash(self):
        digest = self.build(wrong_entry_hash=True)
        with self.assertRaisesRegex(ValueError, "Entry SHA-256"):
            verify(self.path, digest)

    def test_unlisted_payload(self):
        digest = self.build(extra="Forte_Dataset_Handoff_20261017/unlisted.txt")
        with self.assertRaisesRegex(ValueError, "complete archive"):
            verify(self.path, digest)

    def test_traversal_entry(self):
        digest = self.build(extra="Forte_Dataset_Handoff_20261017/../escape")
        with self.assertRaisesRegex(ValueError, "Unexpected archive path"):
            verify(self.path, digest)

    def test_missing_executable_mode(self):
        digest = self.build(executable=False)
        with self.assertRaisesRegex(ValueError, "executable permission"):
            verify(self.path, digest)


if __name__ == "__main__":
    unittest.main()
