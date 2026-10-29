# Микросервисная архитектура — лабораторная работа № 1

Декомпозиция системы бронирования отелей. Вариант 1115.

[Условие задания](https://guest-ivisaev-lab-generator.cloud.cosm-lab.science/?variant=1115).

- [Отчёт в Markdown](LR1_Variant_1115.md).
- [Итоговый PDF](output/pdf/LR1_Variant_1115_Revised.pdf).
- [Google Docs](https://docs.google.com/document/d/1eXtPdXMNdDPKCVOrCXKCP5sE4fetTwiUSfPjV-jt7Cw/edit) — доступ определяется разрешениями документа.

## Диаграммы

| Диаграмма | Просмотр | Исходник |
|---|---|---|
| Context Map | [SVG](diagrams/context-map.svg) | [PlantUML](diagrams/context-map.puml) |
| C4 C2: приложения и клиентские вызовы | [SVG](diagrams/c4-applications.svg) | [PlantUML](diagrams/c4-applications.puml) |
| C4 C2: владельцы хранилищ | [SVG](diagrams/c4-data.svg) | [PlantUML](diagrams/c4-data.puml) |

## Сборка PDF

Нужны Python 3.10 или новее и шрифты Noto Serif, Noto Sans, DejaVu Sans, DejaVu Sans Mono в обычном, полужирном, курсивном и полужирном курсивном начертаниях. Сборщик ищет TTF в системных каталогах и папке `fonts/`; другой каталог можно указать переменной `REPORT_FONT_DIR`. Для основного текста также поддерживаются Times New Roman, Arial и Courier New.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/requirements-pdf.txt
.venv/bin/python tools/build_report_pdf.py
```

PDF собирается из Markdown и готовых SVG в `output/pdf/LR1_Variant_1115_Revised.pdf`.

После изменения исходников схем обновить SVG с помощью PlantUML с установленным Graphviz:

```sh
plantuml -tsvg diagrams/context-map.puml diagrams/c4-applications.puml diagrams/c4-data.puml
```

При содержательных правках поддерживать одинаковое содержание Markdown, PDF и Google Docs.
