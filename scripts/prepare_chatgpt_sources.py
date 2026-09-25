"""Build a safe, organized source bundle for attaching to a ChatGPT project."""

from __future__ import annotations

from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "chatgpt_sources"
INTERFACE_DIR = "01 — Интерфейс и графики"
CORE_DIR = "02 — Основной код бота"
UPLOAD_DIR = "ЗАГРУЗИТЬ В CHATGPT"

INTERFACE_FILES = (
    "charts.py",
    "goals_card.py",
    "reserve_card.py",
    "bracket_card.py",
    "income_colors.py",
    "semantic_chart_colors.py",
)
CORE_FILES = (
    "requirements.txt",
    "bot.py",
    "ui.py",
    "financial_engine.py",
    "storage.py",
    "onboarding.py",
    "settings_editor.py",
    "income.py",
    "income_cycle.py",
    "income_deletion.py",
    "taxes.py",
    "planned_payments.py",
    "period.py",
    "dashboard.py",
    "forecast.py",
    "debts.py",
    "brackets.py",
    "goals_manager.py",
    "calculators.py",
    "allocation_table.py",
    "mode_presentation.py",
    "reserve_help.py",
    "time_utils.py",
    "currency_rates.py",
    "archetypes.py",
)

FILE_MAP = """# Карта файлов проекта для ChatGPT

Этот документ нужно загружать первым. Папки ниже — логические группы: при
загрузке отдельных файлов ChatGPT может не видеть исходную структуру папок.

## Как пользоваться картой

1. Загрузите эту карту.
2. Выберите задачу в таблице ниже и добавьте рекомендованные файлы.
3. Если нужного файла нет среди вложений, ChatGPT должен попросить его, а не
   додумывать содержимое.
4. Не загружайте файлы из документа `00 — НЕ ПРИКЛАДЫВАТЬ.md`.

В инструкции проекта ChatGPT можно написать:

> Сначала сверяйся с файлом «00 — КАРТА ФАЙЛОВ.md». Используй только реально
> загруженные источники. Если для ответа нужен отсутствующий файл, назови его и
> попроси меня добавить, не додумывая его содержимое.

## Служебные документы

| Файл | Назначение |
|---|---|
| `README (набор для ChatGPT).md` | Объясняет устройство подготовленного набора. |
| `README (интерфейс и графики).md` | Описывает группу визуальных модулей. |
| `README (превью).md` | Перечисляет актуальные изображения интерфейса. |
| `README (проект).md` | Исходное общее описание самого бота. |

## Быстрый выбор файлов

| Задача | Какие файлы приложить |
|---|---|
| Общая архитектура бота | `README (проект).md`, `bot.py`, `financial_engine.py`, `storage.py`, `ui.py` |
| Алгоритм распределения денег | `financial_engine.py`, `income.py`, `income_cycle.py`, `brackets.py`, `allocation_table.py` |
| Онбординг и настройки | `onboarding.py`, `settings_editor.py`, `financial_engine.py`, `ui.py` |
| Налоги и плановые платежи | `taxes.py`, `planned_payments.py`, `financial_engine.py`, `storage.py`, `time_utils.py` |
| Доходы и удаление операций | `income.py`, `income_cycle.py`, `income_deletion.py`, `period.py`, `storage.py` |
| Цели и Сундуки | `goals_manager.py`, `goals_card.py`, `financial_engine.py`, `planned_payments.py` |
| Резервы | `reserve_card.py`, `reserve_help.py`, `financial_engine.py`, `settings_editor.py`, `dashboard.py` |
| Интерфейс и диаграммы | `dashboard.py`, `charts.py`, `ui.py`, файлы `*_card.py`, файлы цветов и нужные PNG-превью |
## 01 — Интерфейс и графики

| Файл | Назначение |
|---|---|
| `charts.py` | Общий генератор круговых диаграмм и их легенд. |
| `goals_card.py` | PNG-карточка «Цели и Сундуки». |
| `reserve_card.py` | PNG-карточки Подушки, Стабилизатора и Фонда зарплаты. |
| `bracket_card.py` | PNG-карточка четырёх Бракетов. |
| `income_colors.py` | Палитры и правила различимости цветов типов дохода. |
| `semantic_chart_colors.py` | Постоянные смысловые цвета Целей, Сундуков и балансов. |

### Актуальные превью

| Файл | Что изображено |
|---|---|
| `brackets-piecework-5.png` | Бракеты сдельного профиля. |
| `goals-and-chests-card.png` | Главная карточка Целей и Сундуков. |
| `goals-allocation-chart.png` | Доли распределения между Целями и Сундуками. |
| `income-analysis-chart.png` | Анализ источников дохода. |
| `period-balances-chart.png` | Пополнения конвертов за период. |
| `reserves-stable.png` | Резервы стабильного профиля. |
| `reserves-piecework.png` | Резервы сдельного профиля. |
| `reserves-cyclic.png` | Резервы циклического профиля. |
| `taxes-chart.png` | Налоговая диаграмма. |

## 02 — Основной код бота

| Файл | Назначение |
|---|---|
| `README (проект).md` | Краткое описание проекта и запуск. |
| `requirements.txt` | Python-зависимости. |
| `bot.py` | Точка запуска Telegram-бота и подключение маршрутов. |
| `ui.py` | Общие клавиатуры и форматирование элементов Telegram. |
| `financial_engine.py` | Модели, настройки и основной алгоритм распределения денег. |
| `storage.py` | SQLite-хранилище настроек, состояний и операций. |
| `onboarding.py` | Первичная настройка финансового профиля. |
| `settings_editor.py` | Экраны и сценарии изменения настроек. |
| `income.py` | Ввод, подтверждение и распределение дохода. |
| `income_cycle.py` | Типы финансовых профилей и расчёты циклического дохода. |
| `income_deletion.py` | Безопасное удаление дохода и пересчёт последующих операций. |
| `taxes.py` | Налоговые правила, обязательства, платежи и отчёты. |
| `planned_payments.py` | Расчёт накоплений на запланированные платежи к сроку. |
| `period.py` | Расчётные периоды и операции с ними. |
| `dashboard.py` | Главное меню, аналитика, балансы и отчётные экраны. |
| `forecast.py` | Прогноз распределения будущего дохода. |
| `debts.py` | Долги, кредиты и их погашение. |
| `brackets.py` | Настройки и логика интерфейса Бракетов. |
| `goals_manager.py` | Создание, изменение и распределение Целей и Сундуков. |
| `calculators.py` | Вспомогательные финансовые калькуляторы. |
| `allocation_table.py` | Форматирование таблиц распределения для Telegram. |
| `mode_presentation.py` | Связь финансовых ступеней с изображениями. |
| `reserve_help.py` | Текст справки по сверке резервов. |
| `time_utils.py` | Московские дата и время независимо от часового пояса сервера. |
| `currency_rates.py` | Курсы валют и получение данных ЦБ РФ. |
| `archetypes.py` | Каталог и отображение финансовых архетипов. |

## Важное ограничение

Карта помогает выбрать и связать источники, но не заменяет сами файлы. ChatGPT
не сможет прочитать `taxes.py` или PNG-превью, пока этот файл не добавлен во
вложения текущего проекта или чата.
"""

UPLOAD_BUNDLES = (
    (
        "01 — ПРОДУКТ И ФИНАНСОВАЯ ЛОГИКА.md",
        "Продукт и финансовая логика",
        "Основные сущности, правила распределения, налоги, цели, долги и вспомогательные расчёты.",
        (
            "README.md",
            "financial_engine.py",
            "income_cycle.py",
            "planned_payments.py",
            "taxes.py",
            "debts.py",
            "goals_manager.py",
            "calculators.py",
            "archetypes.py",
            "currency_rates.py",
            "time_utils.py",
        ),
    ),
    (
        "02 — ОНБОРДИНГ И НАСТРОЙКИ.md",
        "Онбординг и настройки",
        "Пользовательские сценарии первой настройки, меню настроек и связанные тексты интерфейса.",
        (
            "onboarding.py",
            "settings_editor.py",
            "ui.py",
            "reserve_help.py",
            "mode_presentation.py",
        ),
    ),
    (
        "03 — ДОХОДЫ, ПЕРИОДЫ И ОТЧЁТЫ.md",
        "Доходы, периоды и отчёты",
        "Ввод доходов, расчётные периоды, главный экран, прогноз и Бракеты.",
        (
            "income.py",
            "period.py",
            "dashboard.py",
            "forecast.py",
            "brackets.py",
            "allocation_table.py",
        ),
    ),
    (
        "04 — ХРАНЕНИЕ И ЦЕЛОСТНОСТЬ ДАННЫХ.md",
        "Хранение и целостность данных",
        "Хранилище, безопасное удаление доходов, точка запуска и зависимости проекта.",
        (
            "storage.py",
            "income_deletion.py",
            "bot.py",
            "requirements.txt",
        ),
    ),
    (
        "05 — ГРАФИКА И ЦВЕТА.md",
        "Графика и цвета",
        "Рендеры диаграмм и карточек, палитры и правила смысловых цветов.",
        INTERFACE_FILES,
    ),
)

UPLOAD_MAP = """# Что загрузить в проект ChatGPT

Это готовый компактный набор для разработки идей, интерфейса и изображений.
Загружайте только файлы из этой папки — обращаться к соседним папкам не нужно.

Всего в наборе **15 файлов**: одна карта, пять текстовых сборников и девять
PNG-превью. Это меньше лимита в 40 файлов.

## Текстовые сборники

| Файл | Что внутри |
|---|---|
| `01 — ПРОДУКТ И ФИНАНСОВАЯ ЛОГИКА.md` | Финансовый движок, профили, налоги, плановые платежи, долги, цели и калькуляторы. |
| `02 — ОНБОРДИНГ И НАСТРОЙКИ.md` | Онбординг, настройки, клавиатуры и пользовательские тексты. |
| `03 — ДОХОДЫ, ПЕРИОДЫ И ОТЧЁТЫ.md` | Доходы, периоды, дашборд, прогноз и Бракеты. |
| `04 — ХРАНЕНИЕ И ЦЕЛОСТНОСТЬ ДАННЫХ.md` | База, удаление дохода, запуск и зависимости — для проверки реализуемости идей. |
| `05 — ГРАФИКА И ЦВЕТА.md` | Исходники диаграмм, карточек и цветовых палитр. |

Каждый исходный файл внутри сборника начинается с отдельного заголовка
`Файл: имя.py`, поэтому ChatGPT может найти нужный модуль по исходному имени.

## PNG-превью

| Файл | Экран |
|---|---|
| `brackets-piecework-5.png` | Бракеты. |
| `goals-and-chests-card.png` | Карточка Целей и Сундуков. |
| `goals-allocation-chart.png` | Распределение между Целями и Сундуками. |
| `income-analysis-chart.png` | Анализ доходов. |
| `period-balances-chart.png` | Балансы расчётного периода. |
| `reserves-stable.png` | Резервы стабильного профиля. |
| `reserves-piecework.png` | Резервы сдельного профиля. |
| `reserves-cyclic.png` | Резервы циклического профиля. |
| `taxes-chart.png` | Налоги. |

## Инструкция для проекта ChatGPT

Скопируйте в настройки проекта:

> Сначала прочитай файл «00 — ЧТО ЗАГРУЖЕНО И ГДЕ ИСКАТЬ.md». Для визуальных
> предложений сверяйся с PNG-превью и сборником «05 — ГРАФИКА И ЦВЕТА.md».
> Для продуктовых решений проверяй соответствующую финансовую логику. Называй
> исходные файлы, на которые опираешься. Если данных не хватает, не додумывай
> реализацию, а попроси нужный источник.

## Не загружать

Не добавляйте `.env`, базы SQLite, резервные копии, `.git`, `__pycache__`,
`*.pyc` и `.DS_Store`. В этом готовом наборе таких файлов нет.
"""

def copy(source_name: str, destination: Path) -> None:
    source = ROOT / source_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def build_source_bundle(
    destination: Path,
    title: str,
    description: str,
    source_names: tuple[str, ...],
) -> None:
    lines = [
        f"# {title}",
        "",
        description,
        "",
        "Это автоматически собранная копия. Исходные файлы проекта не изменены.",
        "",
        "## Содержание",
        "",
    ]
    lines.extend(f"- `{source_name}`" for source_name in source_names)
    for source_name in source_names:
        source = ROOT / source_name
        language = "python" if source.suffix == ".py" else "text"
        lines.extend((
            "",
            "---",
            "",
            f"## Файл: `{source_name}`",
            "",
            f"~~~~{language}",
            source.read_text(encoding="utf-8").rstrip(),
            "~~~~",
        ))
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_upload_ready_bundle() -> None:
    upload = OUTPUT / UPLOAD_DIR
    shutil.rmtree(upload, ignore_errors=True)
    upload.mkdir(parents=True, exist_ok=True)
    (upload / "00 — ЧТО ЗАГРУЖЕНО И ГДЕ ИСКАТЬ.md").write_text(
        UPLOAD_MAP.strip() + "\n",
        encoding="utf-8",
    )
    for filename, title, description, source_names in UPLOAD_BUNDLES:
        build_source_bundle(upload / filename, title, description, source_names)
    for source in sorted((ROOT / "docs" / "previews").glob("*.png")):
        shutil.copy2(source, upload / source.name)

def clean_export_junk() -> None:
    """Remove Finder and Python cache files from the upload-only bundle."""
    for path in OUTPUT.rglob(".DS_Store"):
        path.unlink(missing_ok=True)
    for path in OUTPUT.rglob("*.pyc"):
        path.unlink(missing_ok=True)
    for path in sorted(OUTPUT.rglob("__pycache__"), reverse=True):
        if path.is_dir():
            shutil.rmtree(path)


def remove_ambiguous_readmes() -> None:
    """Remove old generic README copies after switching to descriptive names."""
    old_paths = (
        OUTPUT / "README.md",
        OUTPUT / INTERFACE_DIR / "README.md",
        OUTPUT / INTERFACE_DIR / "Превью" / "README.md",
        OUTPUT / CORE_DIR / "README.md",
    )
    for path in old_paths:
        path.unlink(missing_ok=True)


def write_guides() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "README (набор для ChatGPT).md").write_text(
        "# Источники для ChatGPT\n\n"
        "Это автоматически подготовленные копии. Они не участвуют в работе бота.\n"
        "Редактировать нужно оригиналы в корне проекта, а затем обновлять набор:\n\n"
        "```bash\npython3 scripts/prepare_chatgpt_sources.py\n```\n\n"
        "- `00 — КАРТА ФАЙЛОВ.md` — загрузите первой: в ней содержание и наборы файлов по задачам.\n"
        f"- `{INTERFACE_DIR}/` — код визуальных компонентов и актуальные PNG-превью.\n"
        f"- `{CORE_DIR}/` — основной код бота и финансового движка.\n"
        "- `00 — НЕ ПРИКЛАДЫВАТЬ.md` — файлы, которые нельзя прикладывать.\n",
        encoding="utf-8",
    )
    (OUTPUT / "00 — КАРТА ФАЙЛОВ.md").write_text(
        FILE_MAP.strip() + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "00 — НЕ ПРИКЛАДЫВАТЬ.md").write_text(
        "# Не прикладывать к ChatGPT\n\n"
        "Эти файлы могут содержать секреты, пользовательские финансовые данные "
        "или локальную служебную информацию:\n\n"
        "```text\n"
        ".env\n"
        ".env.production\n"
        "allocator.db\n"
        "allocator.db-shm\n"
        "allocator.db-wal\n"
        "backups/\n"
        ".git/\n"
        "__pycache__/\n"
        "*.pyc\n"
        ".DS_Store\n"
        "```\n\n"
        "В этой папке перечислены только названия: сами закрытые файлы сюда не копируются.\n",
        encoding="utf-8",
    )
    interface = OUTPUT / INTERFACE_DIR
    interface.mkdir(parents=True, exist_ok=True)
    (interface / "README (интерфейс и графики).md").write_text(
        "# Анализ интерфейса и графиков\n\n"
        "Здесь находятся копии модулей визуализации и актуальные PNG-превью. "
        "Набор предназначен для анализа, а не для запуска отдельно от бота.\n\n"
        "Оригинальные Python-модули остаются в корне проекта, чтобы не менять "
        "существующие импорты.\n",
        encoding="utf-8",
    )
    core = OUTPUT / CORE_DIR
    core.mkdir(parents=True, exist_ok=True)
    (core / "О папке.md").write_text(
        "# Основной код бота\n\n"
        "Здесь находятся безопасные копии основных модулей, `requirements.txt` "
        "и оригинального проектного `README.md`. Значение `BOT_TOKEN` в коде "
        "не хранится: бот получает его из закрытого `.env`, который в набор не копируется.\n",
        encoding="utf-8",
    )


def main() -> None:
    interface = OUTPUT / INTERFACE_DIR
    core = OUTPUT / CORE_DIR

    write_guides()
    for source_name in INTERFACE_FILES:
        copy(source_name, interface / source_name)
    for source_name in CORE_FILES:
        copy(source_name, core / source_name)
    copy("README.md", core / "README (проект).md")

    preview_output = interface / "Превью"
    preview_output.mkdir(parents=True, exist_ok=True)
    for source in sorted((ROOT / "docs" / "previews").glob("*.png")):
        shutil.copy2(source, preview_output / source.name)
    copy("docs/previews/README.md", preview_output / "README (превью).md")

    remove_ambiguous_readmes()
    build_upload_ready_bundle()
    clean_export_junk()
    print(f"Prepared ChatGPT sources in {OUTPUT}")


if __name__ == "__main__":
    main()
