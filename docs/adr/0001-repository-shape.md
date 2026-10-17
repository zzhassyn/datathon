# ADR-0001. Форма репозитория: один uv-проект сервиса, pnpm-приложение и отдельное окружение обучения

> Назначение: зафиксировать, как устроены пакеты, окружения и lock-файлы репозитория.
> Владелец: S0 (документ, `pyproject.toml`, `uv.lock`); затрагивает S2 (`web/`) и S3 (`training/`). Статус: черновик v1, 17.10.2026.

## Статус

Принято. Предусловие: ветка `training-plan` (`training/` с `kassa_train` и скриптами) влита в `main` до P0.

## Контекст

- Продукт состоит из трёх частей: сервис на Python (API и воркеры), веб-интерфейс, обучение моделей.
- `training/` уже существует и используется на Mac:
  - окружение `.venv-train` из `training/requirements.txt`;
  - пакет `kassa_train` (`common.py`, `weights.py`);
  - нумерованные скрипты `00_check_env` … `12_train_cls`, обёртки `run_stage_a.sh` и `run_stage_c.sh`.
- Обучение тянет Grounding DINO, pandas и openpyxl. Label Studio закрепляет свои версии numpy и Django. В окружении сервиса это лишний вес или прямые конфликты.
- Окно версий Python узкое:
  - PyAV 19.0.1 требует Python ≥ 3.12;
  - coremltools 9.0 даёт колёса macOS arm64 только до cp313;
  - у 3.12 самое широкое покрытие колёсами на macOS arm64, win_amd64 и linux x86_64.
- Код пишут агенты в параллельных worktree на Windows. Прод — Mac. Перенос — Linux. `uv sync` должен проходить на всех трёх платформах.
- Модели Core ML должны собираться ровно теми версиями ultralytics, coremltools и numpy 2.3.5, что работают в рантайме.

## Решение

1. **Сервис — один корневой uv-проект.**
   - `pyproject.toml` и один универсальный `uv.lock` описывают пакет `src/cashguard` (src layout).
   - `requires-python = '>=3.12,<3.13'`, `.python-version` = `3.12`.
   - `[tool.uv] environments`: darwin-arm64, win32-AMD64, linux-x86_64.
   - Extras:
     - `ml`: ultralytics, torch, torchvision, lap, opencv-python;
     - `mac`: coremltools, mlx, mlx-vlm, llguidance (маркеры darwin + arm64);
     - `cuda`: transformers (маркер linux; индекс PyTorch CUDA через `tool.uv.sources`).
   - Группа `dev` — по умолчанию.
   - S0 объявляет **все** известные зависимости на P0, до начала параллельной работы. Новая зависимость — только через S0.
2. **`web/` — pnpm-пакет.** pnpm 10.34.7 через поле `packageManager`, Node 24.22.0 в `.node-version`, установка всегда `--frozen-lockfile` ([ADR-0014](0014-frontend-stack.md)).
3. **`training/` остаётся как есть.** Своё окружение `.venv-train`, пакет `kassa_train`, нумерованные скрипты. Сервис его **никогда** не импортирует (контракт import-linter). Label Studio живёт в `.venv-ls`.
4. **Экспорт Core ML и проверка паритета — в окружении сервиса.** `10_export_coreml.py` запускается интерпретатором `.venv/bin/python`, а не `.venv-train` ([ADR-0009](0009-perception-neural-engine.md)).
5. **Одинаковые пины в обоих окружениях.** Версии ultralytics, torch и numpy в `uv.lock` и `training/requirements.txt` совпадают точно. Версии записываются в манифест каждой модели.
6. Команды синхронизации:
   - Windows: `uv sync`;
   - Mac: `uv sync --extra ml --extra mac`;
   - перед окном жюри: `uv sync --locked --extra ml --extra mac`.
7. `.gitignore` дополняется `var/` и `.claude/worktrees/`.

## Рассмотренные альтернативы

| Альтернатива | Почему отклонена |
|---|---|
| Подпапка `backend/` для сервиса | Не даёт изоляции сверх src layout. Удлиняет пути в командах, документах и конфигурации инструментов |
| uv workspace из 3–4 членов | Несколько `pyproject.toml` и связи между ними. Один lock и один venv для деплоимого пакета проще для агентов |
| Группа зависимостей `train` внутри lock сервиса | Grounding DINO, pandas, openpyxl и пины Label Studio раздувают окружение сервиса или конфликтуют с ним |
| Отдельные репозитории для фронтенда и бэкенда | Контракт OpenAPI начинает дрейфовать между репозиториями. Сборка same-origin и drift-проверка типов требуют одного дерева |

## Последствия

Плюсы:
- Агенту достаточно одного `uv sync`. Маркеры платформ позволяют синхронизироваться на Windows без Mac-колёс.
- Зависимости обучения не ломают рантайм.
- Пакеты .mlpackage собраны версиями рантайма.

Минусы и цена:
- Два Python-окружения нужно держать согласованными. Сейчас `training/requirements.txt` использует плавающие версии (`ultralytics>=8.4.179`, numpy без пина, `transformers>=5.0`). S3 приводит их к точным пинам ([architecture.md, раздел 15](../architecture.md#15-расхождения-с-текущим-кодом-training)).
- Для экспорта на Mac нужен синхронизированный venv сервиса с `--extra ml --extra mac`.
- Lock-файлы: при конфликте `uv.lock` взять версию `main` и выполнить `uv lock`; при конфликте `pnpm-lock.yaml` — `pnpm install`. Руками не сливать.

Что проверяет решение:
- import-linter: никто не импортирует `training/` и `kassa_train`.
- `uv run just check` на каждом PR.
- Манифест модели содержит версии ultralytics, coremltools и torch, которыми она экспортирована.

Связанные решения: [ADR-0009](0009-perception-neural-engine.md), [ADR-0012](0012-contracts-first-ports-replay.md), [ADR-0014](0014-frontend-stack.md). Обзор: [architecture.md](../architecture.md).
