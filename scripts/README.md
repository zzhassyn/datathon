# `scripts/` — служебные скрипты

> **Назначение:** Вспомогательные скрипты разработки, оценки и эксплуатации. Вызываются из рецептов `just`.
> **Владелец:** S1, S6 — по файлам · **Статус:** доступные скрипты перечислены ниже; будущие утилиты — в roadmap.

Установка и актуальные профили запуска — в [главном README](../README.md).

## Проверка stub

```sh
python scripts/make_synthetic_mov.py /tmp/cashguard-clean.mov --verdict clean
python scripts/make_synthetic_mov.py /tmp/cashguard-violation.mov --verdict violation --duration 145
python scripts/port.py
bash scripts/smoke_jury.sh http://127.0.0.1:8100 "$JURY_KEY" /tmp/cashguard-violation.mov
```

Генератор создаёт небольшой MOV с H.264 video через PyAV, без Pillow/cv2,
без чтения датасета. Исход определяется только `comment=cg-stub:<verdict>`.
Smoke выполняет настоящий curl POST + polling, проверяет frozen jury response
через renderer и пишет полный JSON только локально в `var/smoke/` (либо
`$CG_DATA_DIR/smoke/`). На экран выводится только сводка без кадров/base64.

`python scripts/gen_key.py` запускает человек: ключи сохраняются в
`~/.config/cashguard/.env` с mode 0600, не печатаются и не заменяются без
`--rotate`. Временный путь можно указать через `--path`. Перед переносом на
Windows нужно проверить ACL файла ключей; POSIX mode сам по себе не задаёт ACL.

Окружение — `uv sync --locked`, скрипты запускаются через `uv run`. Smoke берёт
`CG_PYTHON`, затем `.venv/bin/python`, затем `python3`.

## Доступные скрипты

| Файл | Что делает |
|---|---|
| `port.py` | [S1] порт API worktree: хэш пути → 8100–8199 |
| `gen_key.py` | [S1] ключи jury и team через `secrets.token_urlsafe(32)`; пишет прямо в `~/.config/cashguard/.env` (chmod 600), на экран не выводит; запускает человек |
| `make_synthetic_mov.py` | [S1] синтетические H.264-ролики; `--verdict violation\|clean` пишет метатег `comment=cg-stub:<verdict>`, `--duration` задаёт длительность |
| `smoke_jury.sh` | [S1] ровно curl из ТЗ + опрос до `done`; позиционные аргументы `<URL> <ключ> <файл>`; полный ответ — в `var/smoke/result_<job_id>.json`, на экран — только сводка |
| `gen_ui_examples.py` | [S1] эталонные UI-ответы из контрактов и каталога; `--check` проверяет их актуальность |
| `start_local_analysis.ps1` | [S1] запуск локального PT-профиля на Windows; инструкция — [local_analysis_windows.md](../docs/local_analysis_windows.md) |

Планы дополнительных утилит: [roadmap](../docs/roadmap.md) и [структура проекта](../docs/structure.md).

## Правила

- Shell-скрипты с LF.
- Проверки разработки используют синтетические входы и числовые отчёты; банковские медиа остаются локально.

## Документы

- [docs/structure.md — дерево, владельцы, имена](../docs/structure.md)
- [docs/eval.md — метрики и оценка](../docs/eval.md)
- [docs/runbook_mac.md — эксплуатация Mac](../docs/runbook_mac.md)
