# Документация CashGuard — индекс

> **Назначение:** список всех документов проекта с одной строкой о каждом, соответствие разделу 8 ТЗ и правила оформления.
> **Владелец:** S0 (интегратор) · **Статус:** черновик v1, 17.10.2026

## С чего начать

| Кто вы | Что читать |
|---|---|
| Жюри, проверяющий | [../README.md](../README.md) → [violation_catalog.md](violation_catalog.md) → [legal_sources.md](legal_sources.md) → [limitations.md](limitations.md) |
| Агент или разработчик | [../AGENTS.md](../AGENTS.md) → [structure.md](structure.md) → [roadmap.md](roadmap.md) → [architecture.md](architecture.md) → документ своей зоны |
| Эксплуатация Mac | [runbook_mac.md](runbook_mac.md) → [jury_mode.md](jury_mode.md) → [training.md](training.md) |

## Документы в `docs/`

| Документ | Что внутри | Владелец |
|---|---|---|
| [roadmap.md](roadmap.md) | Фазы P0–P5, гейты G0–G5 (соответствие гейтам архитектуры G0–G3 — roadmap, раздел 3), окна Mac, календарь, открытые вопросы с решениями по умолчанию | S0 |
| [architecture.md](architecture.md) | Процессы и режимы `serve`/`train`/`dev`, поток данных, память, надёжность, Linux-вариант | S0 |
| [adr/](adr/) | Архитектурные решения ADR-0001…0014: контекст, альтернативы, причины | S0 |
| [api.md](api.md) | API жюри `/api/v1`, API интерфейса `/api/ui`, `/health` и `/ready`, коды ошибок, примеры curl | S1 |
| [pipeline.md](pipeline.md) | Этапы от проверки файла до рендера: сроки от t0, уровни perception, сцена, правила, VLM, кадры-доказательства | S0, разделы — S1, S3–S6 |
| [violation_catalog.md](violation_catalog.md) | Все коды нарушений и сигналов: название для жюри, группа, признаки, правило, таймкод, норма | S0 (v0 и правки по PR владельцев кодов S4, S5, S6) |
| [ui.md](ui.md) | Экраны, состояния задачи, компоненты, тексты интерфейса, мобильная вёрстка | S2 |
| [training.md](training.md) | Данные, разметка, обучение детектора и классификаторов, экспорт в Core ML, перевод модели в работу | S3 |
| [eval.md](eval.md) | Метрики, LOSO по сценам L1–L5, `eval-replay`, базовая линия, `jury-sim`, `perf` | S1 |
| [runbook_mac.md](runbook_mac.md) | Установка и работа Mac: LaunchAgents, туннель, режимы, ключи, диагностика | S6 |
| [jury_mode.md](jury_mode.md) | Чек-листы: заморозка (Ч+29,5–30), T-2h, T-1h и T-15min от начала окна жюри; проверка с отказами, действия после окна проверки | S6 |
| [limitations.md](limitations.md) | Ограничения решения и известные случаи ошибок | S0 |
| [legal_sources.md](legal_sources.md) | Акты РК, пункты, дословные цитаты, сила нормы и принцип сопоставления «норма → нарушение → кадр» | S5 |
| [structure.md](structure.md) | Дерево репозитория с владельцами, имена, что генерируется, пути Windows и Mac, как добавить тип нарушения или сигнал | S0 |
| `violation_types.md` | Появится позже. Генерируется `scripts/render_docs.py` из `configs/catalog/`, руками не правится | генерируется |

## Соответствие ТЗ, раздел 8

| Что требует ТЗ | Где |
|---|---|
| Адрес сервиса с интерфейсом, адрес API и ключ для жюри | [../README.md](../README.md), раздел «Адреса и доступ» |
| Инструкция по запуску и использованию | [../README.md](../README.md), [runbook_mac.md](runbook_mac.md), [ui.md](ui.md) |
| Архитектура, подход и модели | [architecture.md](architecture.md), [pipeline.md](pipeline.md), [training.md](training.md), [../README.md](../README.md) |
| Нормативные источники и принцип сопоставления правил с видео | [legal_sources.md](legal_sources.md) |
| Перечень типов нарушений с описанием | [violation_catalog.md](violation_catalog.md) |
| Ограничения и известные ошибки | [limitations.md](limitations.md) |

## Архитектурные решения (ADR)

Файлы — в [adr/](adr/), имя `NNNN-<slug>.md`; оглавление — [adr/README.md](adr/README.md).

| № | Решение |
|---|---|
| [0001](adr/0001-repository-shape.md) | Форма репозитория: один uv-проект сервиса, pnpm-приложение `web/`, отдельное окружение обучения |
| [0002](adr/0002-three-process-runtime-supervisor.md) | Три процесса (`api`, `perception`, `vlm`) под кроссплатформенным супервизором, который держит launchd |
| [0003](adr/0003-sqlite-wal-queue-state.md) | SQLite WAL — единственная очередь и хранилище состояния |
| [0004](adr/0004-auth-before-body-streaming-upload.md) | Проверка ключа до чтения тела, потоковая загрузка; 200 только после сохранения задачи |
| [0005](adr/0005-deadline-tiers-provisional-degradation.md) | Сроки от прихода запроса, предварительный результат всегда, лестница деградации |
| [0006](adr/0006-frozen-jury-contract.md) | Замороженный контракт жюри, заранее отрендеренный и проверенный `jury.json` |
| [0007](adr/0007-ui-polling-sse-deferred.md) | Прогресс в интерфейсе — опрос раз в 1 с с предварительными результатами; SSE отложен |
| [0008](adr/0008-separate-ui-api.md) | Отдельный `/api/ui`: без ключа, лимиты по IP, URL вместо base64, mp4-превью |
| [0009](adr/0009-perception-neural-engine.md) | Perception на Neural Engine: флаги экспорта Core ML, две ориентации, явный трекер |
| [0010](adr/0010-qwen-vlm-bounded-verifier.md) | Qwen3.5-4B через mlx-vlm как ограниченный верификатор, который только понижает баллы |
| [0011](adr/0011-pts-timecodes-frame-store.md) | Таймкоды из PTS через PyAV и хранилище сэмплированных кадров на диске |
| [0012](adr/0012-contracts-first-ports-replay.md) | Порты с бэкендами stub, replay и null; replay-трассы содержат только числа |
| [0013](adr/0013-public-ingress-tunnel.md) | Публичный вход: named Cloudflare Tunnel, резерв — Tailscale Funnel |
| [0014](adr/0014-frontend-stack.md) | Frontend: React 19 + Vite 8 + TS 5.9.3 + Tailwind/shadcn + TanStack Query, типы из OpenAPI |

## Документы вне `docs/`

| Документ | Что внутри |
|---|---|
| [../AGENTS.md](../AGENTS.md) | Правила для агентов: контракт жюри, команды, владение, версии, запреты, Definition of Done |
| [../CLAUDE.md](../CLAUDE.md) | Подключает `AGENTS.md` и добавляет заметки только для Claude Code |
| `web/AGENTS.md` | Правила для агентов во фронтенде |
| `README.md` в каждой папке кода | Назначение папки, владелец, какие файлы планируются |
| [../analysis/case_analysis.md](../analysis/case_analysis.md) | Анализ кейса и датасета; раздел 7 — проверенные нормы РК (единственный источник ссылок) |
| [../analysis/detection_design.md](../analysis/detection_design.md) | Как ловится каждое нарушение, покрытие 55 пунктов (группы A, B1, B2, C1, C2), бюджет Mac |
| [../analysis/labels_typed.csv](../analysis/labels_typed.csv) | Типизированная разметка обучающих роликов |
| `../analysis/normative/*.txt` | Тексты актов для дословных цитат |
| [../training/README.md](../training/README.md) | План обучения на Mac, этапы A–D |

`analysis/` — исследование, его не правят. `training/` — зона S3.

## Правила оформления

- В начале документа 2–3 строки: назначение, владелец (S0–S6), статус.
- Текст на русском, короткие предложения. Код, пути и команды — на английском.
- Ссылки между документами — относительные.
- Числа берутся из источников. Оценки помечаются «оценка», непроверенное — «проверить».
- Нормы цитируются только по [../analysis/case_analysis.md](../analysis/case_analysis.md), раздел 7, и дословно по `analysis/normative/*.txt`.
- Документ обновляется в том же PR, что и код, который он описывает.
