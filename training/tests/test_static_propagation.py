"""Регрессия: размножение неподвижных предметов при импорте из Label Studio (05_labelstudio_import.py).

Запуск из корня репозитория (нужен только PyYAML):
    python -m unittest discover -s training/tests -v
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

TRAINING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINING))
from kassa_train import common  # noqa: E402


def _load_import_module():
    spec = importlib.util.spec_from_file_location("ls_import", TRAINING / "scripts" / "05_labelstudio_import.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses ищут модуль в sys.modules
    spec.loader.exec_module(mod)
    return mod


ls = _load_import_module()
CLASSES = common.load_classes()
ID = {c["name"]: c["id"] for c in CLASSES}
STATIC = {c["id"] for c in CLASSES if c.get("static")}
VID = "X-7"


def box(name: str, cx: float, cy: float = 0.5, w: float = 0.1, h: float = 0.1) -> common.Box:
    return common.Box(ID[name], cx, cy, w, h)


def stem(t: float) -> str:
    return common.frame_stem(VID, t)


class StaticPropagationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.labels = root / "labels"
        self.prelabels = root / "prelabels"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def prelabel(self, t: float, boxes: list[common.Box]) -> None:
        common.write_yolo_labels(self.prelabels / VID / f"{stem(t)}.txt", boxes)

    def item(self, t: float, boxes: list[common.Box], origin: str, modified: list[bool] | None = None):
        mods = modified if modified is not None else [False] * len(boxes)
        return ls.Reviewed(VID, stem(t), t, [ls.copy_box(b, "ls") for b in boxes], list(mods), origin)

    def run_propagation(self, items) -> dict[str, str]:
        reviewed = {it.stem: it for it in items}
        return ls.propagate_static(reviewed, STATIC, ID["safe_closed"], ID["safe_open"], self.labels, self.prelabels)

    @staticmethod
    def count(it, name: str) -> int:
        return sum(1 for b in it.boxes if b.cls == ID[name])

    def test_deleted_false_static_box_is_not_restored(self):
        """Кадр 1: человек удалил ложный auth_detector; кадр 2: принятая предразметка с ним же."""
        fake = box("auth_detector", 0.3)
        laptop = box("laptop", 0.7)
        self.prelabel(1.0, [fake, laptop])
        self.prelabel(2.0, [fake, laptop])
        f1 = self.item(1.0, [laptop], "annotation")
        f2 = self.item(2.0, [fake, laptop], "prediction")
        refs = self.run_propagation([f1, f2])
        self.assertEqual(self.count(f1, "auth_detector"), 0, "удалённая человеком рамка вернулась")
        self.assertEqual(f1.added, 0)
        self.assertEqual(self.count(f2, "auth_detector"), 0, "ложная рамка не убрана из непроверенного кадра")
        self.assertEqual(self.count(f2, "laptop"), 1)
        self.assertIn("удалено", refs[f"{VID}:{ID['auth_detector']}"])

    def test_later_deletion_is_kept_in_its_own_frame(self):
        """Кадр 1 правлен (рамка сдвинута), кадр 2 — человек удалил рамку: в кадр 2 ничего не добавляется."""
        self.prelabel(1.0, [box("counting_machine", 0.5)])
        self.prelabel(2.0, [box("counting_machine", 0.5)])
        f1 = self.item(1.0, [box("counting_machine", 0.55)], "annotation", [True])
        f2 = self.item(2.0, [], "annotation")
        f3 = self.item(3.0, [], "prediction")
        self.run_propagation([f1, f2, f3])
        self.assertEqual(self.count(f2, "counting_machine"), 0)
        self.assertEqual(self.count(f3, "counting_machine"), 1, "в кадр без рамки исправленная не добавлена")

    def test_open_safe_frame_is_not_a_deletion_reference(self):
        """Сейф открыт: safe_closed удалён по состоянию — в других кадрах закрытый сейф остаётся."""
        closed = box("safe_closed", 0.8)
        self.prelabel(1.0, [closed])
        self.prelabel(2.0, [closed])
        f1 = self.item(1.0, [box("safe_open", 0.8, w=0.15)], "annotation", [True])
        f2 = self.item(2.0, [closed], "prediction")
        self.run_propagation([f1, f2])
        self.assertEqual(self.count(f1, "safe_closed"), 0)
        self.assertEqual(self.count(f2, "safe_closed"), 1)

    def test_deleting_duplicate_keeps_one_box_everywhere(self):
        """Две рамки safe_closed в предразметке, человек удалил лишнюю на первом кадре."""
        real, dup = box("safe_closed", 0.8), box("safe_closed", 0.6)
        self.prelabel(1.0, [real, dup])
        self.prelabel(2.0, [real, dup])
        f1 = self.item(1.0, [real], "annotation")
        f2 = self.item(2.0, [real, dup], "prediction")
        self.run_propagation([f1, f2])
        self.assertEqual(self.count(f2, "safe_closed"), 1)
        self.assertAlmostEqual(next(b for b in f2.boxes if b.cls == ID["safe_closed"]).cx, 0.8)

    def test_deletion_from_previous_import_applies(self):
        """Прошлый импорт: ложная дверь удалена; новый экспорт — непроверенные кадры с ней."""
        door = box("door", 0.2)
        self.prelabel(1.0, [door])
        self.prelabel(5.0, [door])
        common.write_yolo_labels(self.labels / VID / f"{stem(1.0)}.txt", [])
        f5 = self.item(5.0, [door], "prediction")
        self.run_propagation([f5])
        self.assertEqual(self.count(f5, "door"), 0)

    def test_untouched_static_box_is_copied_to_frames_without_it(self):
        laptop = box("laptop", 0.7)
        f1 = self.item(1.0, [laptop], "prediction")
        f2 = self.item(2.0, [], "prediction")
        self.run_propagation([f1, f2])
        self.assertEqual(self.count(f2, "laptop"), 1)
        self.assertEqual(f2.added, 1)


if __name__ == "__main__":
    unittest.main()
