# `scripts/` — служебные скрипты

**Продолжение backend S1:** сначала прочитать
[README_BACKEND_HANDOFF.md](README_BACKEND_HANDOFF.md). В нём сохранены worktree,
ветка, клонирование на другой компьютер, установка окружения, проверки и следующие
шаги для любой ИИ-сессии (ChatGPT/Codex или другого инструмента).

> **Назначение:** Вспомогательные скрипты разработки, оценки и эксплуатации. Вызываются из рецептов `just`.
> **Владелец:** S1, S6 — по файлам · **Статус:** `port`, `gen_key`, `make_synthetic_mov`, `smoke_jury` реализованы для P0 synthetic stub.

## Проверка stub

```sh
python scripts/make_synthetic_mov.py /tmp/cashguard-clean.mov --verdict clean
python scripts/make_synthetic_mov.py /tmp/cashguard-violation.mov --verdict violation --duration 145
python scripts/port.py
bash scripts/smoke_jury.sh http://127.0.0.1:8100 "$JURY_KEY" /tmp/cashguard-violation.mov
```

Генератор создаёт небольшой MOV с H.264 video через PyAV, без Pillow/cv2,
без чтения датасета. Исход определяется только `comment=cg-stub:<verdict>`.
HEVC/rotation-набор остаётся последующим расширением ingest-тестов.
Smoke выполняет настоящий curl POST + polling, проверяет frozen jury response
через renderer и пишет полный JSON только локально в `var/smoke/` (либо
`$CG_DATA_DIR/smoke/`). На экран выводится только сводка без кадров/base64.

`python scripts/gen_key.py` запускает человек: ключи сохраняются в
`~/.config/cashguard/.env` с mode 0600, не печатаются и не заменяются без
`--rotate`. Временный путь можно указать через `--path`. Перед переносом на
Windows нужно проверить ACL файла ключей; POSIX mode сам по себе не задаёт ACL.

Пока S0 не добавил `pyproject.toml`, `uv.lock` и рецепты, используйте Python
из подготовленного окружения с `PYTHONPATH=src`. Скрипт smoke автоматически
использует `.venv/bin/python`, либо `CG_PYTHON`, либо `python3`.

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
