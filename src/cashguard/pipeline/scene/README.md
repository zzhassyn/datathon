# `src/cashguard/pipeline/scene/` — модель сцены

> **Назначение:** Из трассы строит зоны, роли, сеансы обслуживания, интервалы состояний и события → `SceneModel`.
> **Владелец:** S4 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `zones.py` | зоны `desk`, `cashier_side`, `client_side`, `safe`, `door`, `counting_machine`, `auth_detector`, `room` — по объектам первых секунд, перепроверка при сдвиге |
| `roles.py` | роли `cashier`, `client`, `collector`, `outsider`, `unknown` по зонам и поведению |
| `sessions.py` | сеансы обслуживания `client` и `collection` |
| `states.py` | `StateInterval` с гистерезисом: экран, сейф, ключ, дверь, деньги и сумка на столе, личные вещи, стакан, телефон, присутствие кассира |
| `events.py` | `SceneEvent`: enter, exit, sit, stand, handoff_cash, cash_to_machine, cash_to_detector, cash_to_safe, cash_from_bag, id_shown, bag_on_desk, cup_to_mouth, phone_in_hand, state_change |
| `presence.py` | модуль присутствия: кто у денег и сейфа, когда кассира нет |
| `build.py` | `scene.build(trace, cfg) -> SceneModel` |

## Правила

- Только Python + numpy: модуль работает в режиме replay на Windows без моделей и Mac.
- Зоны строятся по найденным объектам, не по пикселям: так сцена переносится на новый ракурс.
- Роли — по зонам и поведению, никогда по лицам (FAQ 29).
- Параметры — `configs/scene.yaml`. Тесты — `tests/unit/scene/`.

## Документы

- [docs/pipeline.md — этапы обработки](../../../../docs/pipeline.md)
- [docs/violation_catalog.md — коды нарушений и сигналов](../../../../docs/violation_catalog.md)
