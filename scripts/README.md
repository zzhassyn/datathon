# `scripts/` — служебные скрипты

> **Назначение:** Вспомогательные скрипты разработки, оценки и эксплуатации. Вызываются из рецептов `just`.
> **Владелец:** S1, S6 — по файлам · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `port.py` | [S1] порт API worktree: хэш пути → 8100–8199 |
| `gen_key.py` | [S1] ключи jury и team через `secrets.token_urlsafe(32)`; пишет прямо в `~/.config/cashguard/.env` (chmod 600), на экран не выводит; запускает человек |
| `make_synthetic_mov.py` | [S1] синтетические ролики (H.264, HEVC, с поворотом) для тестов, `jury-sim` и проверок с Windows-ПК; `--verdict violation\|clean` пишет метатег `comment=cg-stub:<verdict>` для режима stub; параметр длительности (ролики по 145 с) — предложение |
| `smoke_jury.sh` | [S1] ровно curl из ТЗ + опрос до `done`; позиционные аргументы `<URL> <ключ> <файл>`; полный ответ — в `var/smoke/result_<job_id>.json`, на экран — только сводка |
| `purge_var.py` | [S1] очистка `var/` после окна проверки |
| `render_docs.py` | [S1] генерация `docs/violation_types.md` из `configs/catalog/` |
| `bench_mac.py` | [S6] замеры на Mac в часы 0–3 → `configs/perf_budget.yaml` и выбор кванта |
| `sync_signals.py` | [S6] копирование `var/signals/` с Mac на Windows по scp (LAN или Tailscale), не через облако |

## Правила

- Shell-скрипты с LF.
- Скрипты, которые читают видео (`bench_mac.py`), запускаются только на Mac; агенты на Windows видео не открывают.

## Документы

- [docs/structure.md — дерево, владельцы, имена](../docs/structure.md)
- [docs/eval.md — метрики и оценка](../docs/eval.md)
- [docs/runbook_mac.md — эксплуатация Mac](../docs/runbook_mac.md)
