# Runbook: CashGuard на MacBook Pro M2 Pro

> **Назначение:** установка сервиса на Mac, режимы `serve`/`train`/`dev`, LaunchAgents, туннель, мониторинг, логи, регулярные операции, действия при сбоях, бэкап и очистка `var/`.
> **Владелец:** S6 (VLM + Mac ops + signals) · **Статус:** черновик v1, 17.10.2026

Связанные документы: [architecture.md](architecture.md) (разделы 3, 8–11), [api.md](api.md), [jury_mode.md](jury_mode.md), [training.md](training.md), [eval.md](eval.md), [limitations.md](limitations.md), [ADR-0002](adr/0002-three-process-runtime-supervisor.md), [ADR-0005](adr/0005-deadline-tiers-provisional-degradation.md), [ADR-0013](adr/0013-public-ingress-tunnel.md).

## Содержание

1. [Коротко](#1-коротко)
2. [Карта машины](#2-карта-машины)
3. [Первичная установка](#3-первичная-установка)
4. [LaunchAgents](#4-launchagents)
5. [Публичный адрес: Cloudflare Tunnel и Tailscale Funnel](#5-публичный-адрес-cloudflare-tunnel-и-tailscale-funnel)
6. [Режимы serve, train, dev](#6-режимы-serve-train-dev)
7. [Мониторинг: health, ready, admin status](#7-мониторинг-health-ready-admin-status)
8. [Логи и трасса задачи](#8-логи-и-трасса-задачи)
9. [Регулярные операции](#9-регулярные-операции)
10. [Действия при сбоях](#10-действия-при-сбоях)
11. [Бэкап и очистка var/](#11-бэкап-и-очистка-var)
12. [Чего не делать](#12-чего-не-делать)
13. [Что проверить](#13-что-проверить)

---

## 1. Коротко

- Один MacBook Pro M2 Pro, 16 ГБ — и боевой хост, и машина обучения. Одновременно — никогда.
- Репозиторий: `~/cashguard`. Окружение: `uv`, Python 3.12, без Docker (в контейнере нет Metal и Neural Engine).
- launchd держит три пользовательских агента: `kz.cashguard.supervisor`, `kz.cashguard.tunnel`, `kz.cashguard.caffeinate`.
- Supervisor запускает три процесса: `api` (uvicorn на `127.0.0.1:8000`), `perception` (Core ML на Neural Engine + CPU), `vlm` (Qwen3.5-4B через MLX на GPU).
- Снаружи сервис виден только через именованный Cloudflare Tunnel. Резерв — Tailscale Funnel.
- Состояние — в `~/cashguard/var`. Ключи — в `~/.config/cashguard/.env`.

Как запускать команды:
- `just <recipe>` в этом документе означает `uv run just <recipe>` из `~/cashguard`.
- В окне жюри — `uv run --no-sync just <recipe>`: так `uv` не трогает окружение и сеть.
- Рецепты с пометкой `[macos]` в `justfile` работают только на Mac. Список рецептов — в [structure.md](structure.md). Точные имена рецептов `services-*` — проверить в `justfile` (владелец S0).

## 2. Карта машины

| Путь | Что там | Кто пишет |
|---|---|---|
| `~/cashguard` | клон репозитория (`git clone git@github.com:zzhassyn/datathon.git ~/cashguard`) | люди, `just deploy` |
| `~/cashguard/.venv` | окружение сервиса: `uv sync --locked --extra ml --extra mac` | `uv` |
| `~/cashguard/.venv-train` | окружение обучения ([../training/README.md](../training/README.md)) | `training/setup_mac.sh` |
| `~/cashguard/.venv-ls` | окружение Label Studio | вручную |
| `~/cashguard/models/` | веса: `pretrained/*.pt`, `hf/Qwen3.5-4B-{8,6,4}bit/`, `{detector,pose,cls_screen,cls_safe_lock,cls_door}/vNNN/` | `training/download_models.sh`, экспорт S3 |
| `~/cashguard/data/` | ролики датасета и производные (только Mac, в git не попадает) | S3 |
| `~/cashguard/runs/` | прогоны обучения Ultralytics | S3 |
| `~/cashguard/var/db/cashguard.sqlite3` (+`-wal`, `-shm`) | очередь и состояние задач | api, воркеры |
| `~/cashguard/var/jobs/<id>/` | файлы задачи: `input.mov`, `preview.mp4`, `frames/`, `evidence/`, `jury.json`, `ui_result.json` | api, воркеры |
| `~/cashguard/var/logs/{api,perception,vlm,supervisor}.jsonl` | логи процессов, ротация 10 МБ × 5 | все процессы |
| `~/cashguard/var/signals/`, `var/eval/` | replay-трассы (только числа) и отчёты оценки | `just extract-signals`, eval |
| `~/cashguard/var/yolo/` | `YOLO_CONFIG_DIR` (настройки Ultralytics для офлайна) | установка |
| `~/.config/cashguard/.env` | ключи и переключатели `CG_*`, права 600 | люди, `just gen-key` |
| `~/.cloudflared/` | `cert.pem`, `<UUID>.json` (учётные данные туннеля), `config.yml` | `cloudflared`, установка |
| `~/Library/LaunchAgents/kz.cashguard.*.plist` | агенты launchd, сгенерированы из `deploy/macos/launchd/*.plist.tmpl` | `just services-install` |
| `~/Library/Logs/cashguard/` | stdout/stderr агентов: только падения и трассировки | launchd |

Почему именно `~/cashguard`:
- В `~/Desktop`, `~/Documents`, `~/Downloads` TCC молча блокирует задания launchd.
- iCloud «Рабочий стол и Документы» выгрузит ролики в облако (FAQ 6) и испортит SQLite WAL.
- Если репозиторий уже клонирован в другое место по [../training/README.md](../training/README.md), перенесите его целиком в `~/cashguard` и пересоздайте `.venv*`: venv хранит абсолютные пути и после переноса ломается.

## 3. Первичная установка

Выполняется один раз, с интернетом. Порядок совпадает с P0 для S3/S6 ([roadmap.md](roadmap.md)).

### 3.1. Требования

| Что | Проверка | Ожидание |
|---|---|---|
| macOS | `sw_vers` | 14 или новее; рекомендуется 15 (нужно для `mx.set_wired_limit`) |
| Архитектура | `uname -m` | `arm64` |
| Свободный диск | `df -h ~` | ≥ 40 ГБ (оценка): модели ≈ 13,6 ГБ (≈ 9,5 ГБ по заголовку `download_models.sh` + 4,11 ГБ Qwen 6-bit), три окружения, кэш uv, видео и `open.zip` (≈ 2,4 ГБ), `runs/`, `data/yolo`, worktree на Mac. `00_check_env.py` сам требует только ≥ 20 ГБ. После загрузок: `du -sh ~/cashguard/{models,data,runs,.venv*} ~/.cache/uv` |
| FileVault | `fdesetup status` | если включён — после перезагрузки нужен ручной вход, иначе агенты не стартуют |
| Homebrew | `brew --version` | установлен |

### 3.2. Инструменты

```bash
brew install uv cloudflared jq
uv --version            # стек зафиксирован на uv 0.13.1; более новый — проверить, что uv.lock читается
cloudflared --version   # в стеке 2026.10.1
```

- Tailscale: приложение для macOS, версия в стеке v1.104.1. Нужен только для резервного URL (раздел 5.2).
- Node 24.22.0 и pnpm 10.34.7 нужны только для сборки `web/dist`. В работе сервиса Node не участвует. Версия Node — из `web/.node-version`. Способ установки Node — любой менеджер версий (проверить, какой примет команда). pnpm ставится явно: `npm i -g pnpm@10.34.7` (не через corepack).
- `just` отдельно ставить не нужно: он приходит из dev-группы uv (пакет `rust-just`).

### 3.3. Клон и окружение сервиса

```bash
git clone git@github.com:zzhassyn/datathon.git ~/cashguard
cd ~/cashguard
uv python install 3.12
uv sync --locked --extra ml --extra mac    # только когда в main есть uv.lock (P0-S0-02, окно DEV Ч+3,75)
```

**Запреты чтения для агентских сессий на Mac (человек, P0-S3-01).** Сессии S3-Mac и S6-Mac работают в `~/cg-wt/<slug>`, а данные, модели и `runs/` есть только в `~/cashguard`. Относительные правила `.claude/settings.json` репозитория (`Read(./data/**)` и т. п.) их не закрывают. Поэтому человек добавляет в пользовательский `~/.claude/settings.json` запреты с абсолютными путями (синтаксис — проверить):

```json
{"permissions": {"deny": [
  "Read(//Users/<user>/cashguard/data/**)",
  "Read(//Users/<user>/cashguard/var/**)",
  "Read(//Users/<user>/cashguard/models/**)",
  "Read(//Users/<user>/cashguard/runs/**/*.jpg)"
]}}
```

Окружение worktree на Mac: `CG_MODELS_DIR=$HOME/cashguard/models`, `KASSA_DATA_DIR=$HOME/cashguard/data`, путь к датасету для `extract-signals` и `eval-full` — настройка S1 (имя — проверить; [roadmap.md](roadmap.md), раздел 4.3).

Проверка версий:

```bash
.venv/bin/python - <<'PY'
import sys, sqlite3
from importlib.metadata import version
print("python", sys.version.split()[0], "| sqlite", sqlite3.sqlite_version)
for p in ["fastapi", "uvicorn", "av", "numpy", "ultralytics", "coremltools", "mlx", "mlx-vlm", "psutil"]:
    print(p, version(p))
PY
```

Ожидаемо ([architecture.md](architecture.md), раздел 7): Python 3.12.x; SQLite ≥ 3.35 (нужен `RETURNING`); fastapi 0.143.3; uvicorn 0.54.0; av 19.0.1; numpy 2.3.5; coremltools 9.0; mlx 0.32.4; mlx-vlm 0.7.8; ultralytics 8.4.179 (пин проверить; запасной — 8.4.178 сразу в обоих окружениях).

### 3.4. Сборка интерфейса

```bash
just build-web          # pnpm --dir web install --frozen-lockfile && pnpm --dir web build
ls web/dist/index.html
```

### 3.5. Модели

Все веса скачиваются заранее. После этого сервису интернет не нужен (ТЗ §7, переносимость).

**YOLO (предобученные, ultralytics/assets v8.4.0)** — существующий скрипт:

```bash
bash training/download_models.sh --no-qwen     # YOLO26 + Grounding DINO (DINO нужен только для обучения)
```

**Qwen3.5-4B (MLX) с закреплёнными ревизиями.** Текущий `training/download_models.sh` ревизии не фиксирует и 6-bit не качает; аддитивный PR P0-S3-02 это добавит. Чтобы не качать Qwen дважды (≈ 8 ГБ, оценка), в P0-S3-01 он качается один раз этими командами:

```bash
HF="uvx --from huggingface_hub==2.2.0 hf"
$HF download mlx-community/Qwen3.5-4B-8bit --revision 1704583a --local-dir models/hf/Qwen3.5-4B-8bit   # 5,16 ГБ, основной
$HF download mlx-community/Qwen3.5-4B-6bit --revision fbb1092a --local-dir models/hf/Qwen3.5-4B-6bit   # 4,11 ГБ, запасной
$HF download mlx-community/Qwen3.5-4B-4bit --revision 0e7ffd5c --local-dir models/hf/Qwen3.5-4B-4bit   # 3,06 ГБ, запасной
```

- Принимает ли `hf` короткий хеш ревизии — проверить. Если нет, взять полный sha коммита на странице репозитория.
- sha256 файлов записать в `configs/models.yaml` (владелец S0, правки от S3 через PR):

```bash
find models/hf/Qwen3.5-4B-8bit -type f -not -path '*/.cache/*' -exec shasum -a 256 {} +
```

**Обученные модели** (detector, pose, `cls_screen`, `cls_safe_lock`, `cls_door`) появляются после этапов C/D. Экспорт в Core ML, паритет и MLComputePlan делает экспортёр сервиса `python -m cashguard.pipeline.perception.export` в окружении СЕРВИСА (`.venv`), в режиме `dev` ([training.md](training.md), раздел 14). Результат: `models/<role>/vNNN/{best.pt, *.mlpackage, manifest.json}`. Выход шагов C4–C5 `run_stage_c.sh` (`models/detect/`, `models/pose/yolo26l-pose.mlpackage`, `models/manifest.json`) сервис не читает.

**Сверка.** Воркеры сверяют sha256 с `configs/models.yaml` при старте и не запускаются при расхождении. Ручная проверка одного файла: `shasum -a 256 <файл>` против значения в `configs/models.yaml`.

### 3.6. Ключи и настройки

```bash
mkdir -p ~/.config/cashguard && chmod 700 ~/.config/cashguard
just gen-key            # (человек) scripts/gen_key.py: два токена secrets.token_urlsafe(32) — jury и team
```

`gen-key` пишет `CG_API_KEYS` прямо в `~/.config/cashguard/.env` (chmod 600) и ключи на экран не выводит. Запускает человек, не агентская сессия: ключ не должен попасть в её журнал.

Файл `~/.config/cashguard/.env` (права 600). Имена — из `core/settings.py` (pydantic-settings, префикс `CG_`, владелец S1):

```dotenv
# ~/.config/cashguard/.env
CG_API_KEYS=jury:<токен-жюри>,team:<токен-команды>
CG_PIPELINE_MODE=full
CG_VISION_BACKEND=coreml
CG_VLM_BACKEND=mlx
CG_VLM_ENABLED=true
CG_VLM_QUANT=auto          # для окна жюри: 8bit, только если just perf в SERVE показал ≥ 3 ГБ свободно на пике; иначе 6bit
CG_HOST=127.0.0.1
CG_PORT=8000
CG_MAX_UPLOAD_MB=100
CG_MIN_FREE_MEM_GB=3.0      # свободно перед VLM-этапом; совпадает с требованием ≥ 3 ГБ на пике
CG_MIN_FREE_DISK_GB=2
CG_RETENTION_HOURS=24       # окно жюри: 0 (очистка выключена) или ≥ 168, см. jury_mode.md, чек-лист заморозки
# Лимиты UI (имена — core/settings.py). Обычный профиль: 5 загрузок за 10 мин на IP, 2 активных, очередь UI 10.
# Профиль «окно жюри» (выставить при заморозке): 30 загрузок за 10 мин на IP, 5 активных, очередь UI 20 (api.md, раздел 6.1)
# CG_UI_RATE_*=
# CG_UI_ACCESS_CODE=      # по умолчанию выключен (FAQ 27)
# CG_DOCS_ENABLED=        # по умолчанию — см. core/settings.py; в офлайн-контуре банка — false (Swagger UI грузит JS с CDN)
```

```bash
chmod 600 ~/.config/cashguard/.env
```

- `CG_DATA_DIR`, `CG_MODELS_DIR`, `CG_WEB_DIST` по умолчанию `var`, `models`, `web/dist` относительно рабочей папки агента (`~/cashguard`). Переопределять не нужно.
- Ключи не попадают в git, логи, скриншоты и общие чаты. В логах пишется только имя принципала (`jury`, `team`).
- Jury-ключ никогда не лимитируется. Team-ключ нужен для `/api/v1/admin/status` и прогонов команды.

### 3.7. Офлайн-окружение

Эти переменные задаются в plist агента supervisor (раздел 4). Supervisor дополнительно применяет их сам (`core/env.py`, `apply_offline_env`).

| Переменная | Значение | Зачем |
|---|---|---|
| `HF_HUB_OFFLINE` | `1` | Hugging Face не ходит в сеть; отсутствующий файл — громкая ошибка |
| `TRANSFORMERS_OFFLINE` | `1` | то же для transformers |
| `HF_HUB_DISABLE_TELEMETRY` | `1` | без телеметрии |
| `YOLO_OFFLINE` | `1` | Ultralytics не ходит в сеть |
| `YOLO_AUTOINSTALL` | `false` | Ultralytics не доустанавливает пакеты в venv |
| `YOLO_CONFIG_DIR` | `/Users/<user>/cashguard/var/yolo` | заранее подготовленные настройки |
| `PYTHONUNBUFFERED`, `PYTHONUTF8` | `1` | логи без буфера, UTF-8 |
| `CG_ENV_FILE` | `/Users/<user>/.config/cashguard/.env` | где лежат ключи |
| `PATH` | `/opt/homebrew/bin:/usr/bin:/bin` | launchd не знает про Homebrew |

Подготовка `YOLO_CONFIG_DIR` (один раз, с интернетом):

```bash
mkdir -p ~/cashguard/var/yolo
YOLO_CONFIG_DIR=~/cashguard/var/yolo ~/cashguard/.venv/bin/yolo settings sync=False
```

- Рамки на кадрах рисуются через cv2, поэтому шрифт Arial от Ultralytics не нужен. Нужен ли он какой-либо другой части сервиса — проверить на тесте с выключенным Wi-Fi.
- `var/yolo/` не удалять при очистке `var/` (раздел 11).

**Проверка локальности (человек).** Выключить Wi-Fi и отключить Ethernet → `just services-restart` → дождаться `/ready` = 200 (прогрев всех моделей офлайн) → curl из ТЗ на 3 роликах против `http://127.0.0.1:8000`. Все должны дойти до `done`. Холодный старт нужен, чтобы поймать сетевые вызовы при импорте и старте процессов (HF hub, настройки и ассеты Ultralytics). Это гейт релиза G4 ([roadmap.md](roadmap.md), раздел 7.3). Агентская сессия на Mac при выключенной сети рвётся, поэтому проверку делает человек.

### 3.8. Агенты launchd

```bash
just services-install       # deploy/macos/install.py: рендер plist в ~/Library/LaunchAgents + bootstrap
plutil -lint ~/Library/LaunchAgents/kz.cashguard.*.plist
launchctl print gui/$(id -u)/kz.cashguard.supervisor | grep -E 'state|pid'
curl -s http://127.0.0.1:8000/health
```

Содержимое агентов — раздел 4. Ожидание: `/health` отвечает сразу, `/ready` становится 200 после прогрева моделей. Первая компиляция для Neural Engine может занять десятки секунд (оценка; замер — `bench_mac.py`).

### 3.9. Настройки macOS для режима serve

| Настройка | Значение |
|---|---|
| Питание | от сети; в настройках аккумулятора включить запрет автоматического сна при выключенном дисплее на питании от сети |
| Режим энергосбережения (Low Power Mode) | выключен |
| Крышка | открыта (закрытая крышка усыпляет Mac даже с `caffeinate`) |
| Автообновления macOS и App Store | выключены: обновление сбрасывает кэш компиляции Neural Engine и может перезагрузить Mac |
| Time Machine | на паузе во время окна жюри |
| Сеть | Ethernet, если есть |
| Время | «Устанавливать время автоматически» включено; вручную часы не менять (дедлайны — это отметки времени) |

Проверка: `pmset -g` (настройки), `pmset -g assertions` (виден ассерт от `caffeinate`), `pmset -g batt` (источник питания).

### 3.10. Приёмка установки на Mac (P2-S6-02)

Гейт G0 проверяется раньше и с Windows-ПК: в это время Mac в режиме TRAIN (P0-S6-02, [roadmap.md](roadmap.md)). Ниже — приёмка сервиса на Mac под launchd, окно SERVE Ч+15 – 16. Перед запуском коннектора на Mac коннектор G0 на Windows остановлен (раздел 5.1).

1. `curl -s http://127.0.0.1:8000/health` → 200; `/ready` → 200 в профиле prod. В stub-профиле `/ready` модели не проверяет ([api.md](api.md), раздел 5; решение S1).
2. С телефона через мобильную сеть: `GET https://<host>/health` → 200.
3. POST без ключа → 401.
4. curl из ТЗ с ключом жюри доходит до `done`, ответ валиден по схеме.
5. Загрузка через интерфейс работает.

Команды для пунктов 2–4 — в [jury_mode.md](jury_mode.md), раздел 3.

## 4. LaunchAgents

Шаблоны — `deploy/macos/launchd/kz.cashguard.{supervisor,tunnel,caffeinate}.plist.tmpl`. Рендер — `deploy/macos/install.py` (владелец S6). launchd не раскрывает `~`, поэтому все пути в plist абсолютные. Файлы plist — только с LF (`.gitattributes`): CRLF ломает plist.

| Агент | Команда | Ключи |
|---|---|---|
| `kz.cashguard.supervisor` | `/Users/<user>/cashguard/.venv/bin/python -m cashguard.cli up --profile prod` | RunAtLoad, KeepAlive, ProcessType=Interactive, ThrottleInterval 5, ExitTimeOut 30, SoftResourceLimits NumberOfFiles 8192, WorkingDirectory `~/cashguard`, переменные из раздела 3.7, stdout/stderr → `~/Library/Logs/cashguard/supervisor.{out,err}.log` |
| `kz.cashguard.tunnel` | `/opt/homebrew/bin/cloudflared tunnel --config /Users/<user>/.cloudflared/config.yml run` | RunAtLoad, KeepAlive, stdout/stderr → `~/Library/Logs/cashguard/tunnel.{out,err}.log` (имена файлов — предложение) |
| `kz.cashguard.caffeinate` | `/usr/bin/caffeinate -ims` | RunAtLoad, KeepAlive |

Правила:
- В plist — абсолютный путь к `python` из `.venv`. Никогда `uv run`: при загрузке он может синхронизировать окружение и пойти в сеть.
- `ProcessType=Interactive` снимает фоновое ограничение CPU и IO, которое macOS применяет к агентам.
- Агенты — пользовательские (`gui/<uid>`), не системные демоны: Metal и Neural Engine надёжно доступны в сессии вошедшего пользователя.
- `caffeinate -ims`: `-i` не даёт уснуть от простоя, `-m` — диску, `-s` — системе, но `-s` действует только от сети. Сон при закрытой крышке не блокирует ни один флаг.

Пример рендера агента supervisor (что должен выдать `install.py`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>kz.cashguard.supervisor</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/USER/cashguard/.venv/bin/python</string>
    <string>-m</string><string>cashguard.cli</string>
    <string>up</string><string>--profile</string><string>prod</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/USER/cashguard</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>ExitTimeOut</key><integer>30</integer>
  <key>SoftResourceLimits</key>
  <dict><key>NumberOfFiles</key><integer>8192</integer></dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <key>PYTHONUTF8</key><string>1</string>
    <key>HF_HUB_OFFLINE</key><string>1</string>
    <key>TRANSFORMERS_OFFLINE</key><string>1</string>
    <key>HF_HUB_DISABLE_TELEMETRY</key><string>1</string>
    <key>YOLO_OFFLINE</key><string>1</string>
    <key>YOLO_AUTOINSTALL</key><string>false</string>
    <key>YOLO_CONFIG_DIR</key><string>/Users/USER/cashguard/var/yolo</string>
    <key>CG_ENV_FILE</key><string>/Users/USER/.config/cashguard/.env</string>
  </dict>
  <key>StandardOutPath</key><string>/Users/USER/Library/Logs/cashguard/supervisor.out.log</string>
  <key>StandardErrorPath</key><string>/Users/USER/Library/Logs/cashguard/supervisor.err.log</string>
</dict>
</plist>
```

Что делает supervisor (`src/cashguard/workers/supervisor.py`, владелец S1; подробно — [architecture.md](architecture.md), раздел 3.3):
- Запускает `api`, затем `perception`, затем `vlm` как новые интерпретаторы (`subprocess.Popen`). Никогда не делает fork: Metal и ObjC-рантайм к fork не готовы.
- Перезапускает упавший процесс с паузой 1, 2, 4 … 30 с.
- Убивает (SIGKILL, после проверки cmdline) воркер, если `alive_at` старше 20 с, или `progress_at` старше 60 с в работе, или старше 300 с при прогреве.
- Опрашивает `http://127.0.0.1:8000/health` каждые 5 с; после 3 неудач подряд перезапускает `api`.
- Опрашивает `/ready` cloudflared каждые 15 с; при сбое пишет в лог и показывает локальное уведомление (osascript).
- При остановке пересылает SIGTERM детям и ждёт 30 с.

Команды launchctl (старые `load`/`unload` устарели):

```bash
UID_=$(id -u)
launchctl bootstrap gui/$UID_ ~/Library/LaunchAgents/kz.cashguard.supervisor.plist   # загрузить и запустить
launchctl bootout   gui/$UID_/kz.cashguard.supervisor                                 # остановить и выгрузить
launchctl kickstart -k gui/$UID_/kz.cashguard.supervisor                              # перезапустить
launchctl print     gui/$UID_/kz.cashguard.supervisor                                 # состояние, pid, код выхода
```

Повторный `bootstrap` уже загруженного агента возвращает ошибку. Рецепты `just mode` и `services-*` должны сначала проверять `launchctl print`.

## 5. Публичный адрес: Cloudflare Tunnel и Tailscale Funnel

Решение и причины — [ADR-0013](adr/0013-public-ingress-tunnel.md). Нужен ли домен — открытый вопрос; по умолчанию купить дешёвый. Если домена нет к концу P0, основным становится Funnel.

### 5.1. Именованный Cloudflare Tunnel (основной)

Один раз, с интернетом:

Шаги `login`, `create` и `route dns` выполняет человек: вход в Cloudflare идёт через браузер (OAuth). Для G0 коннектор этого же туннеля сначала запускается на Windows-ПК (P0-S6-02); на Mac он ставится в P2-S6-02 только после остановки коннектора на Windows.

```bash
cloudflared tunnel login                                   # (человек) браузер; появится ~/.cloudflared/cert.pem
cloudflared tunnel create cashguard                        # UUID и ~/.cloudflared/<UUID>.json
cloudflared tunnel route dns cashguard cashguard.<domain>  # DNS-запись на туннель
```

`~/.cloudflared/config.yml` — рендер из `deploy/cloudflared/config.yml.tmpl`:

```yaml
tunnel: <UUID>
credentials-file: /Users/<user>/.cloudflared/<UUID>.json
protocol: http2                 # UDP 7844 (QUIC) часто закрыт на площадках
metrics: 127.0.0.1:20241
no-autoupdate: true             # предложение: не обновляться посреди окна жюри; ключ проверить
ingress:
  - hostname: cashguard.<domain>
    service: http://127.0.0.1:8000   # именно 127.0.0.1: localhost может уйти в ::1 и дать 502
    originRequest:
      connectTimeout: 10s
  - service: http_status:404
```

Проверка конфигурации:

```bash
cloudflared tunnel ingress validate
cloudflared tunnel ingress rule https://cashguard.<domain>/api/v1/analyze
cloudflared tunnel info cashguard          # активные коннекторы
curl -s http://127.0.0.1:20241/ready       # 200, если туннель подключён
```

Настройки в панели Cloudflare (названия разделов могут отличаться — проверить):
- Bot Fight Mode — выключен. Он может выдать проверку curl или python-клиенту жюри, а на бесплатном плане правила WAF его не обходят.
- Режим «Under Attack» — выключен.
- Правило кэша: обход кэша для путей `/api/*`.

Ограничения бесплатного плана, которые важны:
- тело запроса — до 100 МБ (ролики около 29 МБ проходят);
- ответ origin — не дольше 100 с, иначе 524. Поэтому POST отвечает сразу после сохранения файла.

`uvicorn --timeout-keep-alive 100` больше 90 с keep-alive у cloudflared. Это убирает гонку с 502 на загрузках, которые не повторяются.

**Запрещено:** запускать второй коннектор того же туннеля на другой машине, пока жив коннектор Mac. Коннекторы делят нагрузку, и опрос `GET /api/v1/jobs/{id}` может попасть на машину без этой задачи.

**Quick tunnel** (`cloudflared tunnel --url …`, адрес `*.trycloudflare.com`) — только для дымовых тестов в P0. Адрес меняется при каждом перезапуске. Жюри его не отдаём.

### 5.2. Tailscale Funnel (резерв)

```bash
tailscale funnel --bg 8000      # отдаёт тот же origin по https://<mac>.<tailnet>.ts.net
tailscale funnel status
```

- При первом запуске CLI может попросить включить Funnel и HTTPS-сертификаты в админке tailnet — проверить.
- Путь к CLI в приложении Tailscale для macOS и то, переживает ли `--bg` перезагрузку, — проверить на репетиции.
- Funnel в статусе беты, лимиты полосы не настраиваются и не документированы. Перед сдачей проверить 5 параллельных загрузок по 29 МБ через Funnel.
- Через Funnel нет заголовка `CF-Connecting-IP`. Все пользователи интерфейса попадут в один лимит по IP. Ключ жюри не лимитируется. Для загрузок жюри через UI в окне жюри действует профиль лимитов «окно жюри» ([api.md](api.md), раздел 6.1).
- Адрес Funnel сдаётся организаторам как резервный ([jury_mode.md](jury_mode.md), раздел 7).

## 6. Режимы serve, train, dev

Режимы взаимоисключающие. Переключение — `just mode serve|train|dev` (владелец рецепта S0, логика агентов S6). Режим должен переживать перезагрузку: plist лежат в `~/Library/LaunchAgents` с `RunAtLoad`, и без `launchctl disable` launchd снова поднимет supervisor при следующем входе — рядом с возобновлённым обучением.

| Режим | Что запущено | Публичный URL | Кто использует |
|---|---|---|---|
| `serve` | supervisor (`api` + `perception` + `vlm`), tunnel, caffeinate | работает | жюри, демо |
| `train` | tunnel (отдаёт 502), caffeinate; supervisor выгружен и отключён; обучение через `just train-a` / `just train-c`; сервер Label Studio в `.venv-ls` | 502 | S3 |
| `dev` | `uv run just dev`: тот же supervisor с `--profile dev`, порт 8100–8199 из `scripts/port.py`, режимы `stub`/`replay`/`full` | 502 (туннель смотрит на 8000) | S3, S6 для тестов |

Что делает `just mode` (ожидаемое поведение рецепта):

| Команда | Шаги |
|---|---|
| `just mode serve` | `launchctl enable gui/$UID/kz.cashguard.supervisor`; если агент не загружен — `bootstrap` для supervisor, tunnel, caffeinate; ждать `/ready` = 200 |
| `just mode train` | `launchctl bootout gui/$UID/kz.cashguard.supervisor` (SIGTERM, ожидание 30 с) + `launchctl disable gui/$UID/kz.cashguard.supervisor`; проверить, что процессов сервиса нет (`pgrep -fl cashguard`); показать свободную память |
| `just mode dev` | как `train`, затем подсказать команду `uv run just dev` |
| `just train-a`, `just train-c` [macos] | отказаться стартовать, если `pgrep -f 'cashguard.cli up'` что-то находит; иначе `caffeinate -i bash training/run_stage_{a,c}.sh`. Сами `run_stage_*.sh` не меняются (рецепты — P1-S3-05, PR в `justfile` к S0) |

Правила:
- Никогда не включать `train` или `dev` в окно жюри: публичный URL сразу отдаёт 502.
- Перед `train` дождаться пустой очереди (`just status`). Если задачи остались, после возврата в `serve` они перезапустятся или будут завершены по предварительному результату.
- На Mac одновременно идёт только одна тяжёлая работа: либо S3, либо S6. Во время обучения (P2) S6 работает на Windows ([roadmap.md](roadmap.md)).
- Обучение запускать только через `just train-a` / `just train-c`, не напрямую `run_stage_*.sh`: у скриптов нет проверки, что сервис остановлен.
- Экспорт в Core ML, паритет и MLComputePlan — в `.venv` сервиса, в режиме `dev` ([training.md](training.md), раздел 14).
- `just perf` — только в режиме `serve` ([eval.md](eval.md), раздел 8.1).
- После обучения: выйти из `.venv-train`, `just mode serve`, дождаться `/ready` = 200, проверить версии моделей в admin status.

## 7. Мониторинг: health, ready, admin status

| Проверка | Команда | Нормально |
|---|---|---|
| Жив ли API | `curl -s http://127.0.0.1:8000/health` | 200 `{"status":"ok"}` быстрее 10 мс (оценка), без IO |
| Готов ли сервис | `curl -s -w '\n%{http_code}\n' http://127.0.0.1:8000/ready` | 200; при 503 в теле — имена провалившихся проверок |
| Подробно | `just status` или admin status (ниже) | очередь, воркеры, память, туннель, последние задачи |
| Туннель | `curl -s http://127.0.0.1:20241/ready` | 200 |
| Снаружи | `curl -s https://<host>/health` с другой сети | 200 |
| Память | `sysctl vm.swapusage`, `vm_stat` | swap не растёт |
| Диск | `df -h ~` | ≥ 5 ГБ свободно (порог `/ready`) |

Admin status (только team-ключ; jury-ключ получает 403):

```bash
read -rs TEAM_KEY            # ввести team-ключ без эха и без записи в историю
curl -s -H "X-API-Key: $TEAM_KEY" http://127.0.0.1:8000/api/v1/admin/status | jq .
```

Поля `AdminStatus`: `versions`, `models` (роль → версия, sha256), `queue` (`perception`, `vlm`), `workers`, `recent_jobs` (последние 20: источник, статус, уровни, вердикт, число нарушений, тайминги), `memory` (`available_gb`, `mlx_active_gb`, `mlx_peak_gb`), `disk_free_gb`, `tunnel_ready`.

Проверки `/ready` и что делать при провале:

| Проверка | Условие | Если провалена |
|---|---|---|
| `worker_perception` | heartbeat воркера моложе 10 с | раздел 10.2 |
| `worker_vlm` | то же для vlm | раздел 10.2; при работе только по правилам — см. раздел 13 |
| `models_loaded` | detector, pose, 3 cls и VLM (или запасная квантизация) загружены; в режиме `stub` не проверяется ([api.md](api.md), раздел 5) | проверить sha256 и пути в `configs/models.yaml`, лог воркера |
| `warmup_done` | один пробный прогон на каждую модель; в режиме `stub` не проверяется | подождать: первая компиляция для Neural Engine — десятки секунд (оценка) |
| `free_mem_ok` | свободно ≥ `CG_MIN_FREE_MEM_GB` (3,0 ГБ) | раздел 10.3 |
| `disk_ok` | свободно ≥ 5 ГБ | раздел 10.5 |
| `queue_ok` | очередь ниже предела | наплыв загрузок; задачи жюри идут первыми |

В `/ready` также есть `vlm_quant` (8bit, 6bit или 4bit) и `version`. Точку «готов / деградация» в подвале интерфейса рисует тот же `/ready`.

## 8. Логи и трасса задачи

**Где логи:**
- `var/logs/{api,perception,vlm,supervisor}.jsonl` — JSON-строки structlog, ротация 10 МБ × 5 на процесс. В каждой записи по задаче есть `job_id` и `stage`.
- `~/Library/Logs/cashguard/*.log` — stdout/stderr агентов launchd. Сюда попадают только падения и трассировки.
- В логах никогда нет ключей, base64 и кадров.

**Трасса одной задачи:**

```bash
just trace <job_id>      # сливает var/logs/*.jsonl по job_id и сортирует по времени
```

События задачи и тайминги прямо из SQLite (WAL позволяет читать, не мешая записи):

```bash
DB=~/cashguard/var/db/cashguard.sqlite3
sqlite3 -readonly "$DB" "select at, kind, data_json from job_events where job_id='<job_id>' order by id;"
sqlite3 -readonly "$DB" "select status, stage, perception_level, result_level, verdict, n_violations,
  error_code, degradations_json, timings_json from jobs where id='<job_id>';"
```

Ошибки за последний час (имена полей — по `core/logging.py`, владелец S1; проверить):

```bash
jq -c 'select(.level=="error")' ~/cashguard/var/logs/*.jsonl | tail -n 50
```

Что смотреть в трассе:

| Признак | Значение |
|---|---|
| `perception_level` = `fast` или `min` | не хватало времени, вторая попытка после падения или задача взята из очереди позже t0 + 200 с |
| `degradations_json` содержит `perception_truncated` | perception не успел к `perception_hard_stop` (t0 + 235 с); правила отработали по уже обработанным кадрам |
| `result_level` = `rules` | VLM пропущен; причина — в `degradations_json` (`vlm_deadline`, `vlm_memory`, `vlm_crash`, `vlm_backlog`, `vlm_disabled`) |
| `degradations_json` содержит `deadline_finalize` | задачу закрыл sweeper по предварительному результату на `t0+225 с` |
| `error_code` = `UNSUPPORTED_VIDEO` | файл не декодируется; единственный штатный путь к `failed` |
| `error_code` = `PIPELINE_CRASH` | исчерпаны попытки и нет предварительного результата |
| `linked_to` заполнен | тот же файл уже обрабатывался; ответ берётся из связанной задачи |

## 9. Регулярные операции

### 9.1. Каждый день работы Mac

1. `just status`: воркеры `idle`, очередь пустая, свободно ≥ 3 ГБ, туннель готов.
2. `df -h ~` и `du -sh ~/cashguard/{var,runs,data,models}`.
3. Ошибки в логах (раздел 8).
4. Режим соответствует расписанию Mac ([roadmap.md](roadmap.md)).

### 9.2. Выкатить новую версию (не в окно жюри)

```bash
just deploy       # [macos]
```

Ожидаемые шаги рецепта:
1. `git pull --ff-only`.
2. `uv sync --locked --extra ml --extra mac`.
3. `pnpm --dir web install --frozen-lockfile && pnpm --dir web build`.
4. Сверка sha256 моделей с `configs/models.yaml`.
5. Перезапуск supervisor, ожидание `/ready` = 200.

После выкатки: `bash scripts/smoke_jury.sh http://127.0.0.1:8000 <ключ> <synthetic.mov>` и то же против публичного URL. Интерфейс позиционный, зафиксирован в P0-S1-04; полный ответ скрипт пишет в `var/smoke/`, на экран — только сводку.

### 9.3. Перевести новую модель в работу

1. S3 экспортирует модель экспортёром сервиса в режиме `dev` в `.venv` сервиса, с проверкой паритета (≤ 1 mAP50) и размещения на Neural Engine ([training.md](training.md), раздел 14).
2. Правка `configs/models.yaml` — PR через S0.
3. `just deploy` (или `just services-restart`), ожидание `/ready`.
4. Сверить версии и sha256 в admin status.
5. `just extract-signals` и `just sync-signals`, чтобы replay на Windows шёл на новой модели.

### 9.4. Сменить ключи

```bash
just gen-key                     # (человек) новые токены пишутся прямо в CG_API_KEYS в ~/.config/cashguard/.env, на экран не выводятся
launchctl kickstart -k gui/$(id -u)/kz.cashguard.supervisor
```

Проверить: старый ключ → 401, новый → 200. Перезапуск supervisor перезапускает и `vlm` (загрузка Qwen 10–30 с, оценка; замер — `bench_mac.py`). Делать при пустой очереди.

### 9.5. Синхронизировать replay-трассы на Windows

```bash
just extract-signals     # [macos] perception по 40 обучающим роликам → var/signals/<sha16>__<models_hash>.json.gz
just sync-signals        # scp по LAN или Tailscale на Windows; никогда через облачные хранилища
```

Ролики жюри и загрузки через интерфейс в `var/signals` не попадают никогда.

### 9.6. Обновления ПО

- cloudflared, Tailscale, macOS, Homebrew — не обновлять за 24 часа до окна жюри и во время окна.
- После обновления macOS первая загрузка моделей снова компилирует их для Neural Engine: прогрев дольше.

## 10. Действия при сбоях

Общий порядок: посмотреть → записать время и симптом в журнал инцидентов → действовать по таблице → проверить результат снаружи. В окно жюри вмешиваться только по этой таблице.

### 10.1. Публичный URL не отвечает (502, 530, таймаут)

| Шаг | Команда | Вывод |
|---|---|---|
| 1 | `curl -s http://127.0.0.1:8000/health` | не отвечает → проблема в API (шаг 2); отвечает → туннель (раздел 10.4) |
| 2 | `launchctl print gui/$(id -u)/kz.cashguard.supervisor \| grep -E 'state\|pid\|last exit'` | supervisor жив? |
| 3 | `tail -n 50 ~/Library/Logs/cashguard/supervisor.err.log` | трассировка падения |
| 4 | `lsof -nP -iTCP:8000 -sTCP:LISTEN` | порт занят чужим процессом (например, забытый `just dev`)? |
| 5 | `launchctl kickstart -k gui/$(id -u)/kz.cashguard.supervisor` | перезапуск всего сервиса |

- Supervisor сам перезапускает `api` за ≤ 5 с (оценка; проверяет репетиция №1). Задачи в очереди и в работе не теряются: они в SQLite.
- Загрузки, которые шли в момент падения API, получают 502 или обрыв, клиент их повторяет. Повтор того же файла попадает в дедупликацию по sha256.
- Частые причины падения supervisor: нет `.env`, неверный путь в plist, расхождение sha256 моделей (воркер отказывается стартовать).
- Код Cloudflare 524 — origin не ответил за 100 с. Ошибка 1033 или 530 обычно значит, что коннектор туннеля не подключён (проверить).

### 10.2. Воркер завис

Симптомы: задача долго в `processing`, `progress` не растёт; в `just status` у воркера старый `progress_at`; `/ready` → `worker_perception` или `worker_vlm` провалены.

Что происходит автоматически:
- supervisor убивает воркер, если `progress_at` старше 60 с в работе (300 с при прогреве) или `alive_at` старше 20 с;
- sweeper возвращает задачу в очередь: perception — вторая попытка на уровне `fast`; сбой VLM — задача закрывается по предварительному результату (`result_level=rules`, `vlm_crash`);
- если наступил `finalize_at` (`t0+225 с`), задачу закроет sweeper или ленивое закрытие внутри `GET`;
- если perception не дошёл до предварительного результата, на рубеже `perception_hard_stop` (`t0+235 с`) воркер сам закрывает задачу правилами по обработанным кадрам (`perception_truncated`). Ни одна задача не должна быть в `processing` позже `t0+250 с`; исключение — зависший воркер, его убивает supervisor.

Вручную — только если автоматика не сработала за 2 минуты:

```bash
pgrep -fl "cashguard.workers --role"            # pid воркеров
ps -o pid,etime,rss,command -p <pid>            # убедиться, что это наш воркер
kill -9 <pid>                                   # supervisor поднимет новый
```

- Если завис сам supervisor — `launchctl kickstart -k gui/$(id -u)/kz.cashguard.supervisor`. Перезапустятся все три процесса, `vlm` грузится 10–30 с (оценка). Во время пачки задач лучше убить только воркер.
- После инцидента — `just trace <job_id>`: на каком этапе остановился прогресс.

### 10.3. Нехватка памяти

Симптомы: `/ready` → `free_mem_ok` провалена; в задачах `vlm_memory`; `sysctl vm.swapusage` растёт; уровни `fast`/`min`; `memory.available_gb` < 3 в admin status.

Бюджет ([architecture.md](architecture.md), раздел 8, оценка): macOS 4–5 ГБ, `api` 0,2, `perception` 1,5–2,0, `vlm` 6,0–6,5 (8-bit), cloudflared 0,05; итого ≈ 12–13,7 ГБ. Требование — ≥ 3 ГБ свободно на пике.

По шагам, от мягкого к жёсткому:
1. Закрыть всё лишнее: браузер, IDE, Label Studio, обучение, eval, сессии Claude Code и Codex на Mac. Найти потребителей: `ps -axo pid,rss,command | sort -k2 -nr | head`.
2. Убедиться, что авто-квантизация отработала: `vlm_quant` в `/ready`. При `CG_VLM_QUANT=auto` воркер сам переходит на 6-bit, если после прогрева свободно < 3,0 ГБ, и на 4-bit, если памяти всё ещё мало.
3. Принудительно снизить квантизацию: в `.env` поставить `CG_VLM_QUANT=6bit` (−1,05 ГБ) или `4bit` (−2,1 ГБ). Перезапустить только `vlm`: `kill -TERM <pid vlm>`. Supervisor поднимет его заново, и он прочитает `.env` при старте (проверить на репетиции).
4. Только правила: `CG_VLM_ENABLED=false` в `.env`, затем `launchctl kickstart -k gui/$(id -u)/kz.cashguard.supervisor` при пустой очереди. Результат по правилам — надмножество, настроенное на recall 1.0: пропусков не добавит, ложных срабатываний может стать больше.
5. `perception` сам перезапускается между задачами, если его RSS > 3 ГБ или после 200 задач.

Не делать: не убивать `api`; не запускать очистку `var/` во время обработки.

### 10.4. Туннель упал

Симптомы: локально `/health` = 200, снаружи ошибка; `curl -s http://127.0.0.1:20241/ready` не 200; уведомление от supervisor.

1. Проверить сеть Mac (Ethernet подключён, Wi-Fi есть).
2. `tail -n 50 ~/Library/Logs/cashguard/tunnel.err.log`.
3. `launchctl kickstart -k gui/$(id -u)/kz.cashguard.tunnel`.
4. `cloudflared tunnel info cashguard`: коннектор Mac подключён, других коннекторов нет.
5. Если сеть площадки режет соединение — переключить Mac на другую сеть (телефонная точка доступа). Учесть, что тогда 29 МБ на ролик идут через мобильную сеть.
6. Если Cloudflare недоступен дольше нескольких минут — проверить Funnel (`tailscale funnel status`, запрос с телефона к `https://<mac>.<tailnet>.ts.net/health`) и через связного сообщить организаторам, что работает резервный адрес.

Обработка задач при этом продолжается. Результаты отдаются, когда связь вернётся. Второй коннектор того же туннеля на другой машине не запускать (раздел 5.1).

### 10.5. Диск заполнен

Симптомы: POST → 507 (свободно < 2 ГБ, `CG_MIN_FREE_DISK_GB`); `/ready` → `disk_ok` провалена (< 5 ГБ).

```bash
df -h ~
du -sh ~/cashguard/var/* ~/cashguard/runs ~/cashguard/data ~/cashguard/models
```

1. Удалить каталоги задач старше срока хранения (раздел 11.3).
2. Вне окна жюри: удалить старые прогоны обучения в `runs/`, `uv cache prune`, `brew cleanup`.
3. Не удалять `models/`, `var/db/`, `var/yolo/`.
4. Локальные снимки Time Machine тоже занимают место: `tmutil listlocalsnapshots /` (только посмотреть; удаление — решение человека).

Оценка расхода на задачу: исходник около 29 МБ (у задач жюри удаляется после perception), хранилище кадров около 30 МБ (удаляется после финального результата), `jury.json` 0,3–1,5 МБ.

### 10.6. Mac уснул, перезагрузился или пропало питание

Профилактика: `caffeinate -ims` (агент с KeepAlive), питание от сети, крышка открыта, автообновления выключены.

- **Пропало питание.** MacBook работает от аккумулятора. `-i` продолжает держать Mac от сна по простою; `-s` от аккумулятора не действует. Вернуть питание как можно скорее.
- **Mac уснул.** После пробуждения: `pmset -g log | tail -n 30` (когда и почему уснул), `/ready`, туннель `/ready`, проверка с телефона.
- **Перезагрузка.** При включённом FileVault агенты стартуют только после входа пользователя: нужен человек у Mac. После входа: `launchctl print …` по трём агентам, ждать `/ready`. Задачи в работе вернутся в очередь; просроченные закроются по предварительному результату или получат `failed`, если его нет.
- После обновления macOS прогрев дольше: кэш компиляции Neural Engine сброшен.

### 10.7. Жюри получает 401

1. Заголовок именно `X-API-Key`.
2. В `.env` в `CG_API_KEYS` есть `jury:<токен>`, без пробелов и кавычек внутри.
3. После смены ключа supervisor перезапущен.
4. Проверить с телефона тем же ключом, который передан жюри. Ключ в общий чат не пересылать.

### 10.8. Задача в `failed` или ответ выглядит неверно

- `failed` бывает только при `UNSUPPORTED_VIDEO` (файл не читается) или `PIPELINE_CRASH` (попытки исчерпаны, предварительного результата нет). Смотреть `error_code` в admin status и `just trace <job_id>`.
- Ответ жюри всегда проходит проверку по замороженной схеме. При ошибке рендера срабатывает безопасный запасной рендер ([api.md](api.md)).

### 10.9. Mac недоступен полностью

Резерв — Docker-образ Linux/CUDA на Windows-ПК с RTX 3050, режим только по правилам (`deploy/linux/`, владелец S1).

1. Убедиться, что коннектор Mac отключён: `cloudflared tunnel info cashguard` с Windows-ПК (нужен `cert.pem`).
2. Только после этого запустить на Windows коннектор того же туннеля (копия `<UUID>.json` и `config.yml` готовится заранее, [jury_mode.md](jury_mode.md)).
3. Сообщить организаторам. Задачи, принятые Mac до сбоя, резерв не знает: по ним будет 404.

## 11. Бэкап и очистка var/

### 11.1. Что лежит в var/

| Путь | Содержимое | Бэкап | Очистка |
|---|---|---|---|
| `var/db/` | SQLite: задачи, события, тайминги, кэш результатов. Видео нет | можно, для разбора метрик | после события — удалить |
| `var/jobs/<id>/` | `input.mov`, `preview.mp4`, `frames/`, `evidence/`, `jury.json` (кадры в base64), `ui_result.json` | **никогда** (ТЗ §10: тестовые видео нельзя использовать для обучения, публиковать или передавать третьим лицам; поэтому не бэкапим) | автоматически через `CG_RETENTION_HOURS` (24 ч); в окне жюри очистка выключена, папки удаляются вручную после окна; всё — после события |
| `var/signals/` | replay-трассы обучающих роликов, только числа | копия на Windows через `just sync-signals` | после события |
| `var/eval/` | отчёты оценки | можно (числа) | по решению команды |
| `var/logs/` | логи процессов | не нужен | после события |
| `var/yolo/` | настройки Ultralytics для офлайна | не нужен | **не удалять** до конца работы сервиса |

Автоматика ([architecture.md](architecture.md), раздел 4):
- `input.mov` задачи жюри удаляется сразу после perception.
- `frames/` удаляется после финального результата. `evidence/` остаётся.
- Sweeper раз в час удаляет каталоги задач старше `CG_RETENTION_HOURS` (24 ч). При `CG_RETENTION_HOURS=0` (окно жюри) не удаляет ничего.

### 11.2. Бэкап

- Базу копировать только через SQLite backup, не копированием файла: при работающем сервисе есть `-wal`.

```bash
mkdir -p ~/cashguard-backup
sqlite3 ~/cashguard/var/db/cashguard.sqlite3 ".backup '$HOME/cashguard-backup/cashguard-$(date +%Y%m%d-%H%M).sqlite3'"
```

- Папка бэкапа — вне `var/` и вне iCloud.
- Обученные веса `models/<role>/vNNN/` с `manifest.json` копировать на Windows-ПК по LAN или Tailscale (`scp`). В облачные хранилища — никогда.
- Каталоги задач жюри не копировать никуда.

### 11.3. Очистка

Инструмент — `python -m cashguard.cli purge` (обёртка над `scripts/purge_var.py`, владелец S1). Интерфейс ниже — предложение, согласовать с S1:

| Команда | Что делает |
|---|---|
| `purge --older-than 24h` | каталоги задач старше срока (то же, что делает sweeper) |
| `purge --all-jobs` | все каталоги `var/jobs/*` и строки `result_cache` |
| `purge --dry-run …` | только показать, что будет удалено |

Правила:
- Во время обработки задач очистку не запускать.
- `var/db/`, `var/yolo/` и `models/` очистка не трогает.

После события (полная очистка, порядок — в [jury_mode.md](jury_mode.md), раздел 6):
1. `just mode train` или `bootout` всех трёх агентов.
2. Удалить `var/jobs/`, `var/db/`, `var/logs/`, `var/signals/`, `var/eval/`.
3. Удалить `data/` и `runs/`: там кадры датасета (FAQ 6).
4. Те же папки на Windows-ПК (`var/signals`, отчёты `jury-sim`).
5. Судьбу обученных весов решает команда: они часть решения (FAQ 34), но обучены на данных хакатона (FAQ 6) — уточнить у организаторов.

## 12. Чего не делать

- Не запускать обучение, eval, браузер, IDE рядом с сервисом в режиме `serve`.
- Не использовать `uv run` в plist и не обновлять окружение в окно жюри.
- Не запускать второй коннектор того же туннеля, пока жив коннектор Mac.
- Не отдавать жюри адрес quick tunnel.
- Не включать Bot Fight Mode и кэш на `/api/*`.
- Не класть репозиторий в iCloud, `~/Desktop`, `~/Documents`, `~/Downloads`.
- Не использовать `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` и fork после загрузки Metal.
- Не копировать ролики и кадры жюри куда-либо, не добавлять их в eval и обучение.
- Не открывать результаты задач жюри без нужды для разбора инцидента.

## 13. Что проверить

| Вопрос | Кто |
|---|---|
| Имена рецептов `services-*` и поведение `just mode` | S0 (`justfile`), S6 |
| Принимает ли `hf download` короткий хеш ревизии | S6 |
| Нужен ли шрифт Ultralytics в офлайне хоть где-то | S6, тест с выключенным Wi-Fi |
| Перечитывает ли воркер `vlm` `.env` после `kill -TERM` (смена квантизации без перезапуска API) | S1, S6 на репетиции |
| Должен ли `/ready` требовать `worker_vlm`, когда `CG_VLM_ENABLED=false` | S1 |
| Путь CLI Tailscale и сохранение `funnel --bg` после перезагрузки | S6 |
| Передаёт ли Funnel `X-Forwarded-For` | S1 ([api.md](api.md)) |
| Ключ `no-autoupdate` в `config.yml` cloudflared | S6 |
| Интерфейс `cashguard purge` | S1 |
| Аргументы `scripts/smoke_jury.sh` — закрыт: позиционные `<URL> <ключ> <файл>` (P0-S1-04) | — |
| Поля логов (`level`, `job_id`, `stage`) | S1 (`core/logging.py`) |
| Кто физически у Mac в окно жюри, есть ли Ethernet | люди (открытый вопрос 7) |
