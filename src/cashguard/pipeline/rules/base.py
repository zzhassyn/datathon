"""Базовые классы правил и декоратор регистрации.

Одно правило — один файл в своей группе (``leave/``, ``access/``, ``cashops/``, …)::

    @register_rule
    class SafeOpen(BaseRule):
        code = ViolationCode.SAFE_OPEN
        version = "1"

        def evaluate(self, ctx: RuleContext) -> list[Candidate]:
            ...

Центрального списка нет: ``registry.discover`` находит правила через ``pkgutil``.
"""

from abc import ABC, abstractmethod
from typing import ClassVar

from cashguard.contracts.catalog import SignalCode, ViolationCode
from cashguard.contracts.findings import Candidate, RuleContext, Signal


class BaseRule(ABC):
    """Правило, влияющее на вердикт. ``verdict_impact=False`` — только для кодов без нормы."""

    code: ClassVar[ViolationCode]
    version: ClassVar[str] = "1"
    verdict_impact: ClassVar[bool] = True

    @abstractmethod
    def evaluate(self, ctx: RuleContext) -> list[Candidate]:
        """Кандидаты нарушения по сцене. Пустой список — нарушения нет."""


class BaseSignalRule(ABC):
    """Сигнал для проверяющего. На вердикт не влияет."""

    code: ClassVar[SignalCode]
    version: ClassVar[str] = "1"

    @abstractmethod
    def evaluate(self, ctx: RuleContext) -> list[Signal]:
        """Сигналы по сцене."""


AnyRuleClass = type[BaseRule] | type[BaseSignalRule]

_REGISTERED: dict[str, AnyRuleClass] = {}
"""Классы, прошедшие через ``@register_rule``, по ключу ``module:qualname``.

При повторном импорте модуля (``--reload``, тесты) новая версия класса замещает старую,
поэтому ложных дубликатов кода не бывает."""


def _check_rule_class(cls: AnyRuleClass) -> None:
    if issubclass(cls, BaseRule):
        if not isinstance(getattr(cls, "code", None), ViolationCode):
            msg = f"{cls.__qualname__}: code должен быть ViolationCode"
            raise TypeError(msg)
    elif issubclass(cls, BaseSignalRule):
        if not isinstance(getattr(cls, "code", None), SignalCode):
            msg = f"{cls.__qualname__}: code должен быть SignalCode"
            raise TypeError(msg)
    else:
        msg = f"{cls.__qualname__}: наследуйте BaseRule или BaseSignalRule"
        raise TypeError(msg)


def register_rule[T: (type[BaseRule], type[BaseSignalRule])](cls: T) -> T:
    """Регистрирует правило или сигнал. Проверяет, что код есть в каталоге кодов."""
    _check_rule_class(cls)
    _REGISTERED[f"{cls.__module__}:{cls.__qualname__}"] = cls
    return cls


def registered_classes() -> tuple[AnyRuleClass, ...]:
    return tuple(_REGISTERED.values())
