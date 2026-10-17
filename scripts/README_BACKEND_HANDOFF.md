# CashGuard backend — состояние и продолжение работы

Обновлено: 17.10.2026. Владелец: участник «Backend и интеграция» (S0+S1, см. `AGENTS.md`).
Документ для любого человека или ИИ-агента, который продолжает backend на другой машине.

## 1. Код

- Репозиторий: https://github.com/zzhassyn/datathon
- Backend: ветка `s1/backend-core-jobs`. В `main` попадает только проверенное, fast-forward.
- Контракты (`src/cashguard/contracts/**`), каталог (`configs/catalog`, `configs/legal`),
  `pyproject.toml`, `uv.lock`, `justfile` уже в `main` (контрольная точка K0).

```bash
git clone --branch s1/backend-core-jobs https://github.com/zzhassyn/datathon.git cashguard
cd cashguard
git pull --ff-only
```

Без force push. Если опубликованную ветку нужно переделать — новая кандидатная ветка.

## 2. Окружение

Python 3.12 и uv. Второй lock и `pip install` не используются: только `uv.lock`.

```bash
uv sync --locked                              # Windows / Linux, без ML
uv sync --locked --extra ml --extra mac       # Mac (сервис с моделями)
uv run just check                             # шлюз: ruff, format, basedpyright, import-linter, дрейф схем, pytest
```

`.venv`, `var/`, SQLite, ключи, видео и модели в Git не передаются и для stub не нужны.

## 3. Запуск в демонстрационном режиме (stub)

```bash
export CG_PIPELINE_MODE=stub
export CG_DATA_DIR="$PWD/var"
export CG_API_KEYS='jury:local-synthetic-test,team:local-team-test'
uv run just dev 8127                          # supervisor: API + worker
```

PowerShell: `$env:CG_PIPELINE_MODE='stub'`, `$env:CG_DATA_DIR="$PWD\var"`,
`$env:CG_API_KEYS='jury:local-synthetic-test,team:local-team-test'`, затем `uv run just dev 8127`.

Во втором терминале:

```bash
uv run python scripts/make_synthetic_mov.py var/synthetic/violation.mov --verdict violation
uv run python scripts/make_synthetic_mov.py var/synthetic/clean.mov --verdict clean
CG_PYTHON=.venv/Scripts/python.exe bash scripts/smoke_jury.sh http://127.0.0.1:8127 local-synthetic-test var/synthetic/violation.mov
curl -sS http://127.0.0.1:8127/ready
```

На Mac/Linux `CG_PYTHON` не нужен. Ключи выше — публичные тестовые, только для синтетики.
Реальные ключи создаёт человек: `uv run just gen-key`.

Stub решает исход только по метатегу `comment=cg-stub:clean|violation`. Видео без метатега
получает `failed`, а не «нарушений нет». Каждый ответ UI в этом режиме содержит
`pipeline_mode: "stub"` и деградацию «Демонстрационный режим: AI-модели не подключены…».

## 4. Что реализовано

| Часть | Модули | Суть |
|---|---|---|
| Core | `core/*` | настройки `CG_*`, безопасные пути, атомарная запись с fsync, бюджет времени, structlog |
| Очередь | `jobs/*` | SQLite WAL, CAS-захват, `lease_token` на попытку, lease heartbeat, повторы, дедуп, retention |
| Jury API | `api/routes/jury.py` | `/api/v1/*`, ключ проверяется до чтения тела, потоковый multipart, 401/404/413/415/422/507 |
| UI API | `api/routes/ui.py`, `api/views.py` | загрузка без ключа жюри, опрос, `video.mp4` с Range 206, кадры по URL, каталог с ETag/304 |
| Каталог | `pipeline/legal/catalog.py` | 26 типов и 13 сигналов из `configs/catalog`, нормы с `applies_if`; при пустом основании — `legal_note_ru` |
| Рендер | `pipeline/render/*` | ответ жюри по замороженной схеме, ответ UI проверяется `UiJobView`; не прошёл — честный `failed` |
| Воркеры | `workers/*`, `pipeline/stub.py` | supervisor, spawn, рестарт с backoff, watchdog, корректное завершение |
| Оценка | `eval/*` | метрики видео/событий/типа/таймкода; отсутствующий результат не считается TN |

`/ready` в stub: `pipeline_mode: "stub"`, `models_loaded: false`, готовность без моделей.
`full` и `replay` пока завершаются ошибкой сразу: stub не маскируется под модели.

## 5. Контрольные точки

| Точка | Содержание | Статус |
|---|---|---|
| K0 | контракт UI, OpenAPI, эталонные ответы, каталог, режим и честная готовность | сделано |
| K1 | загрузка из браузера: прогресс, отмена, ошибки 413/415/507 на русском | следующая |
| K2 | полный результат и медиа: перекодирование HEVC и поворота, отсутствующие и повреждённые файлы | |
| K3 | устойчивость: перезапуск, зависший воркер, таймаут, нехватка диска, частичный результат | |
| K4 | реальный пайплайн: наблюдения ML → правила → вердикт; replay-трассы | |

## 6. Инварианты

- API жюри `/api/v1/*` не меняется; схема — `contracts/schemas/jury_job.schema.json`.
- Ключ жюри не встраивается во frontend; UI ходит в `/api/ui/*`.
- Нормы — только из `configs/catalog` и `configs/legal`. Нет основания — так и пишем.
- ML не импортируется в API-процесс (import-linter). Модели — в воркерах.
- Видео банка не открываются агентами и никуда не отправляются; облачных AI-вызовов нет.
- `web/` ведёт frontend, `training/` — ML. Изменения контрактов — отдельным PR раньше зависимых.

## 7. Известные ограничения на Windows

- Тесты symlink требуют Developer Mode: без него пропускаются с причиной.
- Тест SIGSTOP работает только на POSIX.
- Shell-smoke — через Git Bash с `CG_PYTHON`, HTTP-проверки — `curl.exe`, не алиас PowerShell.
