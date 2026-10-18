"""Non-ML settings and provenance for the opt-in Windows pipeline."""

import hashlib
import json
from pathlib import Path


class AnalysisBlocked(RuntimeError):
    """Only explicit, safe operator messages may be sent to a failed job."""


def read_config(path):
    if path is None or not Path(path).is_file():
        raise AnalysisBlocked("Не настроен локальный обработчик и пути обученных моделей.")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def config_fingerprint(path):
    if path is None:
        return "not-configured"
    config = read_config(path)
    h = hashlib.sha256(Path(path).read_bytes())
    calibration = Path(config["calibration_file"])
    h.update(calibration.read_bytes() if calibration.is_file() else b"CALIBRATION_MISSING")
    return h.hexdigest()


def model_fingerprint(path):
    config = read_config(path)
    return hashlib.sha256(json.dumps(config["weights"], sort_keys=True).encode()).hexdigest()


def confirmed_calibration(config, video, *, require_ownership=True):
    path = Path(config["calibration_file"])
    if not path.is_file():
        raise AnalysisBlocked(
            "Не подтверждена зона стола кассира. Откройте локальную калибровку; "
            "итог по правилу заблокирован."
        )
    calibration = json.loads(path.read_text(encoding="utf-8"))
    if not all(
        calibration.get(k) is True
        for k in ("confirmed", "cash_area_confirmed", "staff_only_surface")
    ):
        raise AnalysisBlocked(
            "Не подтверждены границы стола и применимость правила к кассовому узлу."
        )
    if calibration.get("video_sha256") != video["sha256"] or any(
        calibration.get(k) != video[k] for k in ("width", "height", "rotation_deg")
    ):
        raise AnalysisBlocked(
            "Калибровка относится к другому видео или геометрии кадра. "
            "Требуется подтверждение камеры."
        )
    if require_ownership and calibration.get("staff_ownership_confirmed") is not True:
        raise AnalysisBlocked(
            "Принадлежность личных вещей сотруднику не подтверждена. Итог по правилу заблокирован."
        )
    return calibration
