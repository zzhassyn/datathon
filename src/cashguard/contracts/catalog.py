"""Каталог нарушений, сигналов и нормативных актов.

Данные — ``configs/catalog/<CODE>.yaml``, ``configs/catalog/signals.yaml``,
``configs/legal/acts.yaml``.
Нормы — только из ``analysis/case_analysis.md`` §7, цитаты дословно из ``analysis/normative/*.txt``.
"""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from cashguard.contracts._base import Contract


class ViolationCode(StrEnum):
    """Коды, влияющие на вердикт: 17 из датасета (группа A), 8 видимых новых (B1) и прочее."""

    # A: есть примеры в обучающих роликах
    CASH_WITHHELD = "CASH_WITHHELD"
    NO_CLIENT_ID = "NO_CLIENT_ID"
    PREV_CASH_ON_DESK = "PREV_CASH_ON_DESK"
    MIXED_CLIENT_CASH = "MIXED_CLIENT_CASH"
    RECOUNT_WITHOUT_CLIENT = "RECOUNT_WITHOUT_CLIENT"
    NO_AUTH_CHECK = "NO_AUTH_CHECK"
    VALUABLES_LEFT = "VALUABLES_LEFT"
    SAFE_OPEN = "SAFE_OPEN"
    SAFE_KEY_LEFT = "SAFE_KEY_LEFT"
    PC_UNLOCKED = "PC_UNLOCKED"
    DOOR_NOT_CLOSED = "DOOR_NOT_CLOSED"
    OUTSIDER_IN_CASH_ROOM = "OUTSIDER_IN_CASH_ROOM"
    THIRD_PERSON_DURING_SERVICE = "THIRD_PERSON_DURING_SERVICE"
    COLLECTOR_NOT_VERIFIED = "COLLECTOR_NOT_VERIFIED"
    BAG_CASH_NO_RECOUNT = "BAG_CASH_NO_RECOUNT"
    PERSONAL_ITEMS = "PERSONAL_ITEMS"
    FOOD_DRINK = "FOOD_DRINK"
    # B1: видно на видео, в датасете нет
    PHONE_CASHIER = "PHONE_CASHIER"
    LEFT_DURING_SERVICE = "LEFT_DURING_SERVICE"
    PAYOUT_WITHOUT_RECOUNT = "PAYOUT_WITHOUT_RECOUNT"
    CHANGE_BEFORE_PREPARED = "CHANGE_BEFORE_PREPARED"
    STORAGE_UNESCORTED = "STORAGE_UNESCORTED"
    CASH_CONCEALED = "CASH_CONCEALED"
    CASH_TRANSFER_OUTSIDE_OP = "CASH_TRANSFER_OUTSIDE_OP"
    UNATTENDED_PRESENCE = "UNATTENDED_PRESENCE"
    # Новые типы из прохода Qwen по ключевым кадрам
    OTHER_VISIBLE_IRREGULARITY = "OTHER_VISIBLE_IRREGULARITY"


class SignalCode(StrEnum):
    """Сигналы (B2): видны проверяющему в интерфейсе, на вердикт не влияют."""

    SIG_SEAL_NOT_INSPECTED = "SIG_SEAL_NOT_INSPECTED"
    SIG_BAG_OPENED_WITHOUT_COLLECTOR = "SIG_BAG_OPENED_WITHOUT_COLLECTOR"
    SIG_RECOUNT_WITHOUT_SECOND_EMPLOYEE = "SIG_RECOUNT_WITHOUT_SECOND_EMPLOYEE"
    SIG_STAFF_CASH_TRANSFER = "SIG_STAFF_CASH_TRANSFER"
    SIG_DOC_NOT_HANDED = "SIG_DOC_NOT_HANDED"
    SIG_REJECT_TRAY_TO_CLIENT = "SIG_REJECT_TRAY_TO_CLIENT"
    SIG_NO_SIGNATURE_SEEN = "SIG_NO_SIGNATURE_SEEN"
    SIG_NO_STAMP_SEEN = "SIG_NO_STAMP_SEEN"
    SIG_MULTI_DOC_SINGLE_RECOUNT = "SIG_MULTI_DOC_SINGLE_RECOUNT"
    SIG_SAFE_CLOSED_WITHOUT_COUNT = "SIG_SAFE_CLOSED_WITHOUT_COUNT"
    SIG_SAFE_WITHOUT_PC = "SIG_SAFE_WITHOUT_PC"
    SIG_CASH_TO_STAFF = "SIG_CASH_TO_STAFF"
    SIG_CAMERA_OBSTRUCTED = "SIG_CAMERA_OBSTRUCTED"


class ActCode(StrEnum):
    PKO = "PKO"
    """Правила кассовых операций и инкассации, пост. НБРК от 29.11.2019 № 231."""
    POP = "POP"
    """Правила охраны и устройства помещений, пост. НБРК от 24.02.2020 № 14."""
    POO = "POO"
    """Правила обменных операций, пост. НБРК от 04.04.2019 № 49 (только обменный пункт)."""
    TIB = "TIB"
    """Требования к ИБ банков, пост. АРРФР от 03.04.2026 № 53."""


class LegalStrength(StrEnum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    NONE = "none"


class TimecodePolicy(StrEnum):
    """Какой момент становится ``timecode_sec``."""

    EVENT = "event"
    """Момент наблюдаемого действия."""
    EXIT = "exit"
    """Момент, когда кассир покинул рабочее место или помещение."""
    STATE_START = "state_start"
    """Начало недопустимого состояния (сейф открыт и т. п.)."""
    OMISSION_DUE = "omission_due"
    """Момент, когда пропущенный шаг уже должен был произойти (нарушение по бездействию)."""


class ViolationGroup(StrEnum):
    LEAVE = "leave"
    ACCESS = "access"
    CASHOPS = "cashops"
    COLLECTION = "collection"
    DISCIPLINE = "discipline"
    PRESENCE = "presence"
    OTHER = "other"


class LegalRef(Contract):
    """Ссылка на норму. ``quote_ru`` дословно присутствует в ``analysis/normative/*.txt``."""

    act_code: ActCode
    act_title_ru: str = Field(min_length=1)
    clause: str | None = None
    """Пункт/подпункт; можно опустить только по FAQ 31 (ссылка без точного пункта)."""
    quote_ru: str | None = Field(default=None, min_length=1)
    """Дословная цитата пункта. Пусто только у ссылки на акт целиком (``clause`` пуст)."""
    url: str = Field(pattern=r"^https://adilet\.zan\.kz/")
    strength: LegalStrength
    note_ru: str | None = None
    """Оговорка к норме. Если ``clause`` пуст, здесь обязана быть ссылка на «FAQ 31»."""
    applies_if: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    """Имя факта кандидата (``Candidate.facts``): норма цитируется, только если факт истинен.
    Например, п. 22 ПКО — только при ``during_service``."""

    @model_validator(mode="after")
    def _clause_or_faq31(self) -> Self:
        if self.clause is None:
            if self.note_ru is None or "FAQ 31" not in self.note_ru:
                msg = "ссылка без пункта допустима только со ссылкой на FAQ 31 в note_ru"
                raise ValueError(msg)
        elif self.quote_ru is None:
            msg = "у ссылки на пункт обязательна дословная цитата quote_ru"
            raise ValueError(msg)
        return self


class CatalogEntry(Contract):
    """Запись ``configs/catalog/<CODE>.yaml``."""

    code: ViolationCode
    title_ru: str = Field(min_length=1)
    """Стабильная строка поля ``type`` в ответе жюри."""
    group: ViolationGroup
    verdict_impact: bool
    timecode_policy: TimecodePolicy
    template_ru: str = Field(min_length=1)
    """Шаблон описания для ``str.format_map``; поля — из ``required_facts``."""
    required_facts: list[str] = Field(default_factory=list[str])
    vlm_question_ru: str | None = None
    legal: list[LegalRef] = Field(default_factory=list[LegalRef])
    pdf_items: list[int] = Field(default_factory=list[int])
    """Номера пунктов командного списка из 55 сценариев."""
    owner: str = Field(pattern=r"^S[0-6]$")


class SignalEntry(Contract):
    """Запись ``configs/catalog/signals.yaml``. Формулировка — «не наблюдается…», без норм."""

    code: SignalCode
    title_ru: str = Field(min_length=1)
    template_ru: str = Field(min_length=1)
    required_facts: list[str] = Field(default_factory=list[str])
    pdf_items: list[int] = Field(default_factory=list[int])


class SignalCatalog(Contract):
    signals: list[SignalEntry]


class ActStatus(StrEnum):
    IN_FORCE = "in_force"
    REPEALED = "repealed"


class ActEntry(Contract):
    act_code: ActCode
    title_ru: str = Field(min_length=1)
    cite_ru: str = Field(min_length=1)
    """Как ссылаться в описании, например «п. 20 Правил, утв. постановлением …»."""
    issuer: str
    number: str
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    registration_no: str
    adilet_id: str
    url: str = Field(pattern=r"^https://adilet\.zan\.kz/")
    status: ActStatus
    scope_note_ru: str | None = None


class DoNotCite(Contract):
    title_ru: str
    reason_ru: str


class ActsFile(Contract):
    """``configs/legal/acts.yaml``."""

    acts: list[ActEntry]
    do_not_cite: list[DoNotCite]
