# eval/ — правила для агентов

- Владелец: **S1**. Метки читаются только из колонок `file,label,timecode_sec,type_code,type_ru,clean_pair,confidence,scene` файла `analysis/labels_typed.csv`.
- Видео и кадры не открывать: оценка идёт по числовым replay-трассам.
