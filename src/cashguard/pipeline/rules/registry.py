"""Поиск правил через ``pkgutil`` и запуск всех правил по сцене.

Правило попадает в реестр, если его модуль лежит в одном из пакетов поиска и класс отмечен
``@register_rule``. Дубликат кода в одном наборе пакетов — ошибка.
"""

import importlib
import logging
import pkgutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from cashguard.contracts.findings import Candidate, RuleConfig, RuleContext, Signal
from cashguard.pipeline.rules.base import (
    AnyRuleClass,
    BaseRule,
    BaseSignalRule,
    registered_classes,
)

log = logging.getLogger(__name__)

DEFAULT_PACKAGES = ("cashguard.pipeline.rules",)
_SKIP_MODULES = {"base", "registry", "context"}


@dataclass(frozen=True)
class RuleRegistry:
    rules: dict[str, BaseRule] = field(default_factory=dict[str, BaseRule])
    signals: dict[str, BaseSignalRule] = field(default_factory=dict[str, BaseSignalRule])

    @property
    def codes(self) -> set[str]:
        return set(self.rules) | set(self.signals)


def _import_tree(package_name: str) -> None:
    package = importlib.import_module(package_name)
    paths = getattr(package, "__path__", None)
    if paths is None:
        return
    for info in pkgutil.walk_packages(paths, prefix=f"{package_name}."):
        if info.name.rsplit(".", 1)[-1] in _SKIP_MODULES:
            continue
        importlib.import_module(info.name)


def _in_packages(cls: AnyRuleClass, packages: Iterable[str]) -> bool:
    return any(cls.__module__ == p or cls.__module__.startswith(f"{p}.") for p in packages)


def discover(packages: Iterable[str] = DEFAULT_PACKAGES) -> RuleRegistry:
    """Импортирует все модули пакетов и собирает зарегистрированные правила и сигналы."""
    pkgs = tuple(packages)
    for name in pkgs:
        _import_tree(name)
    registry = RuleRegistry()
    for cls in registered_classes():
        if not _in_packages(cls, pkgs):
            continue
        code = str(cls.code)
        if code in registry.codes:
            msg = f"код {code} зарегистрирован дважды (второй раз: {cls.__module__})"
            raise ValueError(msg)
        if issubclass(cls, BaseRule):
            registry.rules[code] = cls()
        else:
            registry.signals[code] = cls()
    return registry


def evaluate_all(
    base: RuleContext,
    configs: Mapping[str, RuleConfig] | None = None,
    registry: RuleRegistry | None = None,
) -> tuple[list[Candidate], list[Signal]]:
    """Запускает все включённые правила и сигналы. Упавшее правило пропускается и пишется в лог."""
    reg = registry if registry is not None else discover()
    cfgs = configs or {}
    candidates: list[Candidate] = []
    signals: list[Signal] = []

    for code, rule in sorted(reg.rules.items()):
        cfg = cfgs.get(code, RuleConfig(code=code))
        if not cfg.enabled:
            continue
        try:
            found = rule.evaluate(base.model_copy(update={"cfg": cfg}))
        except Exception:
            log.exception("правило %s упало и пропущено", code)
            continue
        for cand in found:
            if str(cand.code) != code:
                msg = f"правило {code} вернуло кандидата с кодом {cand.code}"
                raise ValueError(msg)
            if cand.verdict_impact != rule.verdict_impact:
                msg = f"правило {code}: verdict_impact кандидата не совпадает с правилом"
                raise ValueError(msg)
        candidates.extend(found)

    for code, sig in sorted(reg.signals.items()):
        cfg = cfgs.get(code, RuleConfig(code=code))
        if not cfg.enabled:
            continue
        try:
            signals.extend(sig.evaluate(base.model_copy(update={"cfg": cfg})))
        except Exception:
            log.exception("сигнал %s упал и пропущен", code)

    candidates.sort(key=lambda c: (c.t_sec, str(c.code)))
    signals.sort(key=lambda s: (s.t_sec, str(s.code)))
    return candidates, signals
