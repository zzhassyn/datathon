# pipeline/ — правила для агентов

- Владельцы подпапок — в корневом `AGENTS.md`. `runner.py` и `rules/{base,registry,context}.py` — **S0**: сигнатуры стадий заморожены.
- `scene`, `rules`, `verdict`, `legal`, `render` — чистый Python + numpy (работают в replay на Windows). ML-библиотеки — только в `perception`, `decode`, `evidence`, `vlm/backends`.
- Пайплайн не импортирует `api`, `jobs`, `workers`. Группы правил не импортируют друг друга. Одно правило — один файл с `@register_rule`.
