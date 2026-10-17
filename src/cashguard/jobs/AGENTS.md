# jobs/ — правила для агентов

- Владелец: **S1**. SQLite WAL: очередь, состояние, кэш, perf_stats.
- Каждый переход состояния — compare-and-set (`rowcount == 1`). Без ML-библиотек; можно импортировать `core` и `contracts`.
