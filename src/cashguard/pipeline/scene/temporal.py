"""Хронология из числовых треков. Тексты датасета не используются при инференсе."""

import math
from collections import defaultdict

from cashguard.contracts.geometry import BBox
from cashguard.contracts.scene import (
    Role,
    SceneEvent,
    SceneEventKind,
    SceneLayout,
    SceneModel,
    ServiceSession,
    SessionKind,
    StateInterval,
    StateSubject,
    TrackRole,
    Zone,
    ZoneKind,
)


def inside(box, roi):
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return roi[0] <= x <= roi[2] and roi[1] <= y <= roi[3]


def center(box):
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def distance(box, roi):
    a, b = center(box), center(roi)
    return math.hypot(a[0] - b[0], a[1] - b[1])


def overlaps(box, roi):
    return box[0] < roi[2] and box[2] > roi[0] and box[1] < roi[3] and box[3] > roi[1]


def rectangle(roi, kind):
    if len(roi) != 4 or not all(math.isfinite(v) for v in roi):
        raise ValueError("Некорректная геометрия зоны")
    x1, y1, x2, y2 = roi
    if not 0 <= x1 < x2 <= 1 or not 0 <= y1 < y2 <= 1:
        raise ValueError("Зона вне кадра")
    return Zone(kind=kind, polygon=[(x1, y1), (x2, y1), (x2, y2), (x1, y2)], conf=1)


def build_numeric(frames, video, calibration, params=None):
    """Зоны staff/desk подтверждает оператор; неизвестные роли не угадываются.

    cashier_track_id можно задать явно. Иначе нужен ровно один устойчивый трек
    в подтверждённой staff-only зоне в начале. client_roi — отдельная зона;
    без неё другие люди остаются unknown. Для выхода нужен подход к входу,
    направленное движение и наблюдаемое отсутствие; конец видео не есть выход.
    """
    cfg = params or {}
    if not calibration.get("confirmed") or not calibration.get("staff_only_surface"):
        raise ValueError("Сцена требует подтверждённой поверхности кассира")
    if calibration.get("video_sha256") != video["sha256"]:
        raise ValueError("Калибровка относится к другому видео")
    if any(calibration.get(k) != video[k] for k in ("width", "height", "rotation_deg")):
        raise ValueError("Геометрия калибровки не совпадает с видео")
    width, height = video["width"], video["height"]
    desk = [
        v / s for v, s in zip(calibration["desk_roi"], [width, height, width, height], strict=True)
    ]
    zones = [rectangle(desk, ZoneKind.DESK)]
    client = cfg.get("client_roi")
    gate = cfg.get("entry_roi")
    staff_zone = cfg.get("cashier_roi")
    safe_zone = cfg.get("safe_roi")
    for roi, kind in ((staff_zone, ZoneKind.CASHIER_SIDE), (safe_zone, ZoneKind.SAFE)):
        if roi is not None:
            zones.append(rectangle(roi, kind))
    if client is not None:
        zones.append(rectangle(client, ZoneKind.CLIENT_SIDE))
    if gate is not None:
        zones.append(rectangle(gate, ZoneKind.DOOR))
    minimum = int(cfg.get("min_track_frames", 3))
    grace = float(cfg.get("exit_confirm_sec", 2))
    calibration_sec = float(cfg.get("role_initial_sec", 5))
    max_gap = float(cfg.get("max_observation_gap_sec", 0.75))
    if minimum < 2 or not all(
        math.isfinite(v) and v > 0 for v in (grace, calibration_sec, max_gap)
    ):
        raise ValueError("Некорректные параметры времени сцены")
    if not frames:
        raise ValueError("Нет наблюдений для сцены")
    tracks = defaultdict(list)
    previous = -1.0
    for i, frame in enumerate(frames):
        t = frame["t_sec"]
        if not math.isfinite(t) or not previous < t <= video["duration_sec"]:
            raise ValueError("Немонотонная хронология")
        previous = t
        for detection in frame["detections"]:
            if (
                detection["class_name"] == "person"
                and detection.get("track_id") is not None
                and detection["conf"] >= 0.5
            ):
                tracks[detection["track_id"]].append((i, t, detection))
    early = []
    for key, observations in tracks.items():
        hits = [
            d
            for _, t, d in observations
            if staff_zone is not None and t <= calibration_sec and inside(d["box"], staff_zone)
        ]
        if len(hits) >= minimum:
            early.append(key)
    cashier = cfg.get("cashier_track_id")
    if cashier is None and len(early) == 1:
        cashier = early[0]
    if cashier is not None and cashier not in tracks:
        raise ValueError("Заданный трек кассира не наблюдается")
    roles = []
    events = []
    for key, observations in tracks.items():
        role = Role.UNKNOWN
        if key == cashier:
            role = Role.CASHIER
        elif (
            client is not None
            and sum(inside(d["box"], client) for _, _, d in observations) >= minimum
        ):
            role = Role.CLIENT
        first_idx, first_t, first = observations[0]
        _, last_t, _ = observations[-1]
        entered = (
            gate is not None
            and inside(first["box"], gate)
            and first_idx > 0
            and len(observations) >= minimum
            and distance(observations[minimum - 1][2]["box"], gate)
            > distance(first["box"], gate) + 0.005
            and observations[minimum - 1][1] - first_t <= max_gap * minimum
        )
        exited = False
        for n, (idx, t, detection) in enumerate(observations):
            next_idx = observations[n + 1][0] if n + 1 < len(observations) else len(frames)
            missing = frames[idx + 1 : next_idx]
            if gate is None or not missing or missing[-1]["t_sec"] - t < grace or n < 1:
                continue
            before = observations[n - 1][2]["box"]
            closer = distance(detection["box"], gate) + 0.005 < distance(before, gate)
            continuous = all(
                b["t_sec"] - a["t_sec"] <= max_gap
                for a, b in zip(frames[idx : next_idx - 1], frames[idx + 1 : next_idx], strict=True)
            )
            if (
                not inside(detection["box"], gate)
                or not closer
                or not continuous
                or t - observations[n - 1][1] > max_gap
            ):
                continue
            stamp = missing[0]["t_sec"]
            confirmed = next(f["t_sec"] for f in missing if f["t_sec"] - t >= grace)
            empty_until = stamp
            for f in missing:
                if any(d["class_name"] == "person" and d["conf"] >= 0.25 for d in f["detections"]):
                    break
                empty_until = f["t_sec"]
            events.append(
                SceneEvent(
                    kind=SceneEventKind.EXIT,
                    t=stamp,
                    track_ids=[key],
                    conf=0.7,
                    box=BBox(
                        x1=detection["box"][0],
                        y1=detection["box"][1],
                        x2=detection["box"][2],
                        y2=detection["box"][3],
                    ),
                    data={
                        "role": str(role),
                        "confirmed_at": confirmed,
                        "room_empty_until": empty_until,
                        "reason": "gate_motion_then_absence",
                    },
                )
            )
            exited = True
        if entered:
            events.append(
                SceneEvent(
                    kind=SceneEventKind.ENTER,
                    t=first_t,
                    track_ids=[key],
                    conf=0.7,
                    data={"role": str(role)},
                )
            )
        roles.append(
            TrackRole(
                track_id=key,
                role=role,
                conf=0.7 if role != Role.UNKNOWN else 0,
                first_t=first_t,
                last_t=last_t,
                entered_via_door=entered,
                exited_via_door=exited,
            )
        )
    states = []
    active = {}

    def advance(key, subject, value, t, confidence, zone=None, track=None):
        state = active.get(key)
        if state is not None and state["value"] == value and t - state["end_t"] <= max_gap:
            state["end_t"] = t
            state["conf"] = min(state["conf"], confidence)
        else:
            if state is not None:
                states.append(StateInterval(**state))
            active[key] = dict(
                subject=subject,
                value=value,
                start_t=t,
                end_t=t,
                conf=confidence,
                zone=zone,
                track_id=track,
            )

    cashier_exits = [e for e in events if e.kind == SceneEventKind.EXIT and cashier in e.track_ids]
    latest_staff_seen = -1.0
    for frame in frames:
        t = frame["t_sec"]
        people = [
            d for d in frame["detections"] if d["class_name"] == "person" and d["conf"] >= 0.5
        ]
        staff = (
            next((d for d in people if d.get("track_id") == cashier), None)
            if cashier is not None
            else None
        )
        if staff:
            latest_staff_seen = t
        confirmed_exit = any(
            e.t > latest_staff_seen and e.data["confirmed_at"] <= t for e in cashier_exits
        )
        presence = "present" if staff else "absent" if confirmed_exit else "unknown"
        if staff and any(
            h["conf"] >= 0.5 and inside(h["box"], desk) and inside(h["box"], staff["box"])
            for h in frame.get("hands", [])
        ):
            presence = "working"
        advance(
            "cashier",
            StateSubject.CASHIER_PRESENT,
            presence,
            t,
            0.7 if presence != "unknown" else 0,
            track=cashier,
        )
        # Away from a calibrated safe: never inferred merely from an open door.
        safe_busy = safe_zone is not None and any(
            d["class_name"] == "person" and d["conf"] >= 0.25 and overlaps(d["box"], safe_zone)
            for d in frame["detections"]
        )
        away = staff is not None and safe_zone is not None and not safe_busy
        if safe_zone is not None and not safe_busy and (away or presence == "absent"):
            advance(
                "away",
                StateSubject.CASHIER_PRESENT,
                "away_from_safe",
                t,
                0.7,
                ZoneKind.SAFE,
                cashier,
            )
        elif "away" in active:
            states.append(StateInterval(**active.pop("away")))
        seen = set()
        for detection in frame["detections"]:
            cls = detection["class_name"]
            subject = {
                "cash": StateSubject.CASH_ON_DESK,
                "collection_bag": StateSubject.BAG_ON_DESK,
                "personal_bag": StateSubject.PERSONAL_ITEM_ON_DESK,
            }.get(cls)
            key = (cls, detection.get("track_id"))
            if (
                subject is None
                or key[1] is None
                or detection["conf"] < 0.5
                or not inside(detection["box"], desk)
            ):
                continue
            seen.add(key)
            advance(key, subject, cls, t, detection["conf"], ZoneKind.DESK, key[1])
        for key in list(active):
            if isinstance(key, tuple) and key not in seen:
                states.append(StateInterval(**active.pop(key)))
        safe = [
            d
            for d in frame["detections"]
            if d["class_name"] in {"safe_open", "safe_closed"}
            and d["conf"] >= 0.5
            and safe_zone is not None
            and inside(d["box"], safe_zone)
        ]
        if safe and len({d["class_name"] for d in safe}) == 1:
            best = max(safe, key=lambda d: d["conf"])
            value = "open" if best["class_name"] == "safe_open" else "closed"
            advance("safe", StateSubject.SAFE, value, t, best["conf"], ZoneKind.SAFE)
        elif "safe" in active:
            states.append(StateInterval(**active.pop("safe")))
    states.extend(StateInterval(**item) for item in active.values())
    sessions = [
        ServiceSession(
            id=f"client-{role.track_id}",
            kind=SessionKind.CLIENT,
            subject_track_id=role.track_id,
            start_t=role.first_t,
            end_t=next(
                (
                    e.t
                    for e in events
                    if e.kind == SceneEventKind.EXIT and role.track_id in e.track_ids
                ),
                None,
            ),
        )
        for role in roles
        if role.role == Role.CLIENT
    ]
    return SceneModel(
        layout=SceneLayout(
            zones=zones, orientation="operator-calibrated", calibrated_from_sec=0, conf=1
        ),
        roles=roles,
        sessions=sessions,
        states=sorted(states, key=lambda s: (s.start_t, str(s.subject))),
        events=sorted(events, key=lambda e: e.t),
    )


def build(trace, cfg):
    frames = [
        dict(
            t_sec=f.t_sec,
            detections=[
                dict(
                    class_name=str(d.cls),
                    conf=d.conf,
                    box=[d.box.x1, d.box.y1, d.box.x2, d.box.y2],
                    track_id=d.track_id,
                )
                for d in f.detections
            ],
        )
        for f in trace.frames
    ]
    return build_numeric(
        frames, trace.video.model_dump(mode="json"), cfg["calibration"], cfg.get("params")
    )
