#!/bin/zsh
set -e
cd "${0:A:h}/frontend"
if ! command -v node >/dev/null 2>&1; then
  task_runtime="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies"
  if [[ -x "$task_runtime/node/bin/node" ]]; then
    export PATH="$task_runtime/node/bin:$task_runtime/bin/fallback:$PATH"
  fi
fi
if ! command -v node >/dev/null 2>&1; then
  print 'Для запуска установите Node.js 22.12+ и pnpm. Инструкция: frontend/README.md'
  read '?Нажмите Enter для выхода.'
  exit 1
fi
if [[ ! -d node_modules ]]; then
  if command -v pnpm >/dev/null 2>&1; then pnpm install; else npm install; fi
fi
if command -v pnpm >/dev/null 2>&1; then
  exec pnpm dev
else
  exec npm run dev
fi
