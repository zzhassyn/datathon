"""Прогноз времени 08_train_detect.py: загрузчик не масштабируется размером модели, замеры не прогнозируются.

Запуск из корня репозитория (нужен только PyYAML):
    python -m unittest discover -s training/tests -v
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

TRAINING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINING))


def _load_train_module():
    spec = importlib.util.spec_from_file_location("train_detect", TRAINING / "scripts" / "08_train_detect.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


t08 = _load_train_module()


class ProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        # пустой data_dir: датасетов нет, число кадров берётся из замера
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("KASSA_DATA_DIR")
        os.environ["KASSA_DATA_DIR"] = self._tmp.name
        self.cfg = t08.load_train_config()

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("KASSA_DATA_DIR", None)
        else:
            os.environ["KASSA_DATA_DIR"] = self._old
        self._tmp.cleanup()

    def measured(self, **kw) -> dict:
        base = {"model": "yolo26n.pt", "scale": "n", "imgsz": 640, "train_images": 100, "val_images": 50,
                "dataset_train_images": 400, "sec_per_epoch": 12.0, "train_sec_per_epoch": 10.0,
                "data_sec_per_epoch": 6.0, "freeze": None}
        base.update(kw)
        return base

    def test_probe_profiles_are_not_projected(self):
        proj, det = t08.project_profiles(self.measured(), self.cfg)
        self.assertNotIn("smoke", det)
        self.assertNotIn("calib_full", det)
        self.assertIn("full_sec", proj)

    def test_dataloader_time_scales_only_with_image_count(self):
        proj, det = t08.project_profiles(self.measured(), self.cfg)
        full = self.cfg["profiles"]["full"]
        k = (864 ** 2 / 640 ** 2) * (1.0 / 0.15)
        count = 400 * float(full.get("fraction", 1.0)) / 100
        expected_epoch = (4.0 * k + 6.0) * count + 2.0 * (50 / 50) * k
        self.assertAlmostEqual(det["full"]["sec_per_epoch"], round(expected_epoch, 1), places=1)
        self.assertFalse(det["full"]["comparable_measurement"])  # n@640 → l@864 — грубая оценка

    def test_calibration_run_is_comparable_for_full(self):
        m = self.measured(model="yolo26l.pt", scale="l", imgsz=864, freeze=10)
        _, det = t08.project_profiles(m, self.cfg)
        self.assertTrue(det["full"]["comparable_measurement"])
        self.assertTrue(det["full_m"]["comparable_measurement"])
        self.assertFalse(det["pseudo"]["comparable_measurement"])  # pseudo без freeze

    def test_smoke_keeps_mosaic_and_calib_matches_full(self):
        prof = self.cfg["profiles"]
        self.assertEqual(prof["smoke"]["close_mosaic"], 0)
        calib, full = prof["calib_full"], prof["full"]
        for key in ("model", "imgsz", "freeze", "batch"):
            self.assertEqual(calib[key], full[key], key)
        self.assertEqual(calib["close_mosaic"], 0)


if __name__ == "__main__":
    unittest.main()
