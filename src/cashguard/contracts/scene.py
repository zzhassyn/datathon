"""Модель сцены: зоны, роли, сеансы, состояния, события. Строит S4, читают правила.

Роли определяются по зонам и поведению, без распознавания лиц (FAQ 29).
"""

from enum import StrEnum

from pydantic import Field

from cashguard.contracts._base import Contract
from cashguard.contracts.geometry import BBox, Point


class ZoneKind(StrEnum):
    DESK = "desk"
    CASHIER_SIDE = "cashier_side"
    CLIENT_SIDE = "client_side"
    SAFE = "safe"
    DOOR = "door"
    COUNTING_MACHINE = "counting_machine"
    AUTH_DETECTOR = "auth_detector"
    ROOM = "room"


class Zone(Contract):
    kind: ZoneKind
    polygon: list[Point] = Field(min_length=3)
    conf: float = Field(ge=0, le=1)


class SceneLayout(Contract):
    zones: list[Zone]
    orientation: str
    calibrated_from_sec: float = Field(ge=0)
    conf: float = Field(ge=0, le=1)


class Role(StrEnum):
    CASHIER = "cashier"
    CLIENT = "client"
    COLLECTOR = "collector"
    OUTSIDER = "outsider"
    UNKNOWN = "unknown"


class TrackRole(Contract):
    track_id: int
    role: Role
    conf: float = Field(ge=0, le=1)
    first_t: float = Field(ge=0)
    last_t: float = Field(ge=0)
    entered_via_door: bool
    exited_via_door: bool


class SessionKind(StrEnum):
    CLIENT = "client"
    COLLECTION = "collection"


class ServiceSession(Contract):
    id: str
    kind: SessionKind
    subject_track_id: int
    start_t: float = Field(ge=0)
    end_t: float | None = None
    id_shown_t: float | None = None


class StateSubject(StrEnum):
    SCREEN = "screen"
    SAFE = "safe"
    SAFE_KEY = "safe_key"
    DOOR = "door"
    CASH_ON_DESK = "cash_on_desk"
    BAG_ON_DESK = "bag_on_desk"
    PERSONAL_ITEM_ON_DESK = "personal_item_on_desk"
    CUP = "cup"
    PHONE = "phone"
    CASHIER_PRESENT = "cashier_present"


class StateInterval(Contract):
    subject: StateSubject
    value: str
    start_t: float = Field(ge=0)
    end_t: float = Field(ge=0)
    conf: float = Field(ge=0, le=1)
    zone: ZoneKind | None = None
    track_id: int | None = None


class SceneEventKind(StrEnum):
    ENTER = "enter"
    EXIT = "exit"
    SIT = "sit"
    STAND = "stand"
    HANDOFF_CASH = "handoff_cash"
    CASH_TO_MACHINE = "cash_to_machine"
    CASH_TO_DETECTOR = "cash_to_detector"
    CASH_TO_SAFE = "cash_to_safe"
    CASH_FROM_BAG = "cash_from_bag"
    ID_SHOWN = "id_shown"
    BAG_ON_DESK = "bag_on_desk"
    CUP_TO_MOUTH = "cup_to_mouth"
    PHONE_IN_HAND = "phone_in_hand"
    STATE_CHANGE = "state_change"


Scalar = str | int | float | bool


class SceneEvent(Contract):
    kind: SceneEventKind
    t: float = Field(ge=0)
    track_ids: list[int]
    conf: float = Field(ge=0, le=1)
    box: BBox | None = None
    data: dict[str, Scalar] = Field(default_factory=dict[str, Scalar])


class SceneModel(Contract):
    layout: SceneLayout
    roles: list[TrackRole]
    sessions: list[ServiceSession]
    states: list[StateInterval]
    events: list[SceneEvent]
