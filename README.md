# MINECRAFT TRANSLATOR

Автоматический переводчик модов, квестов и конфигов Minecraft
(FTB Quests, Patchouli, SNBT, KubeJS, lang и др.)

---

## РУССКАЯ ВЕРСИЯ

1. О ПРОГРАММЕ

---

Minecraft Translator – это приложение с графическим интерфейсом (GUI), которое
помогает автоматически переводить модпаки Minecraft на другие языки
(например, ru_ru).

Поддерживаются основные форматы:

* lang / en_us.json
* Patchouli (patchouli_books/.../en_us/*.json)
* FTB Quests (*.snbt) – структурный разбор через NBT
* KubeJS (*.js)
* различные JSON-файлы (tips, книги, подсказки, описания)
* lang-файлы внутри jar-модов (автоматический поиск en_us.json)

Программа подходит как для клиентских сборок, так и для серверов.

2. ЧТО УМЕЕТ ПРОГРАММА

---

* зеркально копирует структуру входной папки в выходную;
* переводит почти все человекочитаемые строки (названия, описания, подсказки);
* использует структурный SNBT-парсер: SNBT → NBT → перевод → SNBT;
* сохраняет цветовое форматирование Minecraft (коды вида §7, §a и т.п.);
* аккуратно пропускает технические поля (id, команды, координаты, пути ресурсов);
* старается не ломать файлы: при ошибке оставляет исходный текст;
* поддерживает Python 3.10+;
* может собираться в отдельные приложения для Windows и macOS.

3. ВАЖНО: ОГРАНИЧЕНИЯ ПЕРЕВОДА

---

Программа не волшебная, поэтому:

* не все строчки переводятся идеально – иногда перевод может быть кривым,
  особенно в сложных технических описаниях;
* некоторые строки пропускаются специально (ID, пути ресурсов, типы задач FTB),
  чтобы не ломать моды и не вызывать краши;
* если мод использует свой нестандартный формат или генерирует надписи кодом,
  эти тексты могут не попасть в перевод;
* для полной локализации клиента всё равно нужны корректные ru_ru.json внутри
  самих модов (или отдельный ресурс-пак).

Идея переводчика – дать максимум автоматизации, но итоговый результат всё равно
можно и иногда полезно слегка подредактировать руками.

4. УСТАНОВКА И ЗАПУСК

---

1. Установите Python 3.11 (или совместимую версию 3.10+).

   Проверка:
   python --version

2. Установите зависимости:

   pip install -r requirements.txt

3. Создайте файл secrets.json в корне проекта:

   {
   "api_key": "ВАШ_API_КЛЮЧ"
   }

4. Запустите GUI (pywebview):

   python src/main.py

   Старый Tkinter-интерфейс остаётся как запасной вариант:
   python -m src.gui_main_legacy

   Windows: нужен Microsoft Edge WebView2 Runtime (обычно уже установлен в
   Windows 10/11). На старых системах его можно поставить с сайта Microsoft
   («Evergreen WebView2 Runtime»). macOS/Linux используют системный WebView
   (WKWebView / GTK) — доп. установка не требуется.

4a) ЛОКАЛЬНЫЙ ИНФЕРЕНС (опционально, для режима "local"/"hybrid")

---

Перевод внутри приложения без интернета требует llama-cpp-python. Ставится
отдельно; по умолчанию используются ПРЕБИЛТ-колёса (без Xcode/CMake), версия
закреплена (0.3.30 — целое колесо; 0.3.32 на индексе оказалось битым):

   macOS Apple Silicon (Metal, готовое колесо):
       pip install -r requirements-mac.txt

   Windows x64 (CPU, готовое колесо):
       pip install -r requirements-win.txt

   Windows NVIDIA CUDA (готовое колесо, подставьте версию CUDA, напр. cu124):
       pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 "llama-cpp-python==0.3.30"

   Intel Mac (x86_64): готового колеса НЕТ — только сборка из исходников
   (нужны Xcode Command Line Tools):
       CMAKE_ARGS="-DGGML_METAL=on" pip install --no-binary=llama-cpp-python "llama-cpp-python==0.3.30"

Это не обязательно: режимы "external" (внешний API) и "server" (запущенный
рядом llama-server) работают без llama-cpp-python.

4b) ПРЕДУПРЕЖДЕНИЕ БЕЗОПАСНОСТИ ПРИ ПЕРВОМ ЗАПУСКЕ (приложение без подписи)

---

Готовые сборки (.exe / .app) не имеют платной цифровой подписи, поэтому при
первом запуске система может показать предупреждение. Это ожидаемо и безопасно.
Ту же инструкцию можно открыть внутри приложения — кнопка «❓ Помощь» вверху.

Windows — «Система Windows защитила ваш компьютер»:
   1. Нажмите «Подробнее».
   2. Нажмите появившуюся кнопку «Выполнить в любом случае».
   3. Дальше приложение запускается обычным двойным щелчком.

macOS — «Не удаётся проверить разработчика» или «Приложение повреждено»:
   Способ 1 (через настройки):
     1. Попробуйте открыть приложение (предупреждение — закройте).
     2. Системные настройки → Конфиденциальность и безопасность.
     3. Внизу найдите строку про MinecraftTranslator и нажмите
        «Всё равно открыть».
   Способ 2 (если пишет «повреждено» — снять карантин в Терминале):
     xattr -cr /Applications/MinecraftTranslator.app

   [скриншот Windows SmartScreen — плейсхолдер]
   [скриншот macOS Конфиденциальность и безопасность — плейсхолдер]

5) РАБОТА ЧЕРЕЗ GUI

---

1. В поле "Входная папка" выберите папку модпака или конфигов
   (например: config, kubejs, assets и т.п.).

2. В поле "Папка вывода" задайте папку, куда сохранять переведённые файлы.

3. Выберите язык перевода (например, Russian (ru_ru)).

4. Нажмите "Старт" и дождитесь окончания обработки.

В логах вы увидите, какие файлы:

* переведены успешно;
* пропущены (уже есть переведённая версия);
* обработаны с предупреждениями;
* дали ошибку (такие файлы обычно остаются без изменений).

6. ПОДДЕРЖИВАЕМЫЕ ФОРМАТЫ (КРАТКО)

---

* lang/en_us.json              – перевод в ru_ru.json (или другую локаль);
* Patchouli книги (*.json)     – тексты страниц и заголовков;
* FTB Quests (*.snbt)          – заголовки, описания, текст в квестах;
* tips/*.json                  – подсказки и обучающие тексты;
* KubeJS (*.js)                – строки внутри сценариев;
* jar-моды                     – автоматически ищется assets/*/lang/en_us.json.

Технические ключи (id, типы задач, пути ресурсов вида modid:item_name и т.п.)
преднамеренно не переводятся.

7. СБОРКА ПРИЛОЖЕНИЯ (КРАТКО)

---

Проект содержит единый GitHub Actions workflow с matrix-сборкой на Windows и
macOS параллельно: .github/workflows/build.yml

Собирается через PyInstaller по платформенным спекам. Локальный инференс —
пиннед пребилт-колесо llama-cpp-python, с ЯВНОЙ проверкой import (job падает,
если не работает — никакого тихого continue-on-error):
* Windows (x64):        MinecraftTranslator-win.spec → .exe (+ .zip)
                        артефакт MinecraftTranslator-win-x64  (полный, CPU)
* macOS Apple Silicon:  MinecraftTranslator-mac.spec → .app в .dmg (+ .zip)
                        артефакт MinecraftTranslator-mac-arm64 (полный, Metal)
* macOS Intel:          MinecraftTranslator-mac.spec → .app в .dmg (+ .zip)
                        артефакт MinecraftTranslator-mac-x64-external-only
                        (пребилт-колеса под macOS x86_64 нет → без локального
                         инференса; работают external/hybrid/server-режимы)

Локальная сборка:
   pyinstaller --noconfirm --clean MinecraftTranslator-win.spec   (Windows)
   pyinstaller --noconfirm --clean MinecraftTranslator-mac.spec   (macOS)

Эти файлы не обязательны для обычного пользователя, но удобны для сборки
готовых EXE и приложений. Сборки без code signing (см. п.4b про предупреждение).

8. СТРУКТУРА ПРОЕКТА (ОСНОВНОЕ)

---

minecraft_translator/
src/
main.py                  – точка входа (pywebview GUI)
gui/                     – UI-слой (мост + фронтенд)
api.py                 – класс Api (window.pywebview.api)
web/                   – index.html / style.css / app.js
gui_main_legacy.py       – прежний Tkinter GUI (fallback)
processors/
snbt_structured.py     – структурный SNBT/NBT переводчик
ftb_snbt.py            – старый / запасной парсер SNBT
lang_json.py
generic_json.py
jar_lang.py
kubejs_js.py
dot_lang.py
utils/
helpers.py
config.py
mirrorer.py              – проход по файлам и выбор нужного процессора
detectors.py
translators.py
requirements.txt
secrets.json
README.txt (или README.md)

9. ЛИЦЕНЗИЯ И АВТОРЫ

---

Лицензия: MIT

Проект создавался и дорабатывается Kirill при помощи ChatGPT
(подсказки по коду, логике перевода и обработке форматов Minecraft).

---

## ENGLISH VERSION

1. ABOUT

---

Minecraft Translator is a GUI tool that helps you automatically translate
Minecraft modpacks to other languages (for example ru_ru).

It supports:

* lang / en_us.json
* Patchouli books
* FTB Quests (*.snbt) via structured NBT parsing
* KubeJS scripts (*.js)
* various JSON files (tips, books, guides)
* lang files inside mod jars (en_us.json)

Works for both client and server modpacks.

2. FEATURES

---

* mirrors the structure of the input directory;
* translates most human-readable strings (titles, descriptions, hints);
* uses a structural SNBT → NBT → translated → SNBT pipeline;
* preserves Minecraft formatting codes (§a, §7, etc.);
* skips technical fields (ids, commands, coordinates, resource locations);
* tries not to break files – on error they are kept as is;
* supports Python 3.10+;
* can be built into standalone Windows and macOS apps.

3. IMPORTANT: LIMITATIONS

---

The translator is powerful but not perfect:

* not every line is translated ideally – some texts may be rough or awkward;
* some fields are intentionally skipped (IDs, resource paths, task types) to
  avoid crashes and broken mods;
* mods with fully custom formats or dynamically generated text may not be
  translated at all;
* for a fully localized client you still may need proper ru_ru.json files
  inside the mods or a custom resource pack.

The goal is to automate as much as possible, but manual polishing of some
translations is still allowed and sometimes recommended.

4. INSTALL AND RUN (LOCAL)

---

1. Install Python 3.11 (or 3.10+).

   Check:
   python --version

2. Install dependencies:

   pip install -r requirements.txt

3. Create secrets.json in the project root:

   {
   "api_key": "YOUR_API_KEY"
   }

4. Run GUI (pywebview):

   python src/main.py

   Legacy Tkinter UI is kept as a fallback:
   python -m src.gui_main_legacy

   Windows requires the Microsoft Edge WebView2 Runtime (preinstalled on
   Windows 10/11; on older systems install the "Evergreen WebView2 Runtime").
   macOS/Linux use the system WebView (WKWebView / GTK).

4a) LOCAL INFERENCE (optional, for "local"/"hybrid" modes)

---

Offline in-app translation needs llama-cpp-python, installed separately. By
default it uses PREBUILT wheels (no Xcode/CMake); the version is pinned (0.3.30
is intact; 0.3.32 on the index was corrupted):

   macOS Apple Silicon (Metal, prebuilt wheel):
       pip install -r requirements-mac.txt

   Windows x64 (CPU, prebuilt wheel):
       pip install -r requirements-win.txt

   Windows NVIDIA CUDA (prebuilt wheel, set your CUDA version, e.g. cu124):
       pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 "llama-cpp-python==0.3.30"

   Intel Mac (x86_64): no prebuilt wheel exists — build from source
   (needs Xcode Command Line Tools):
       CMAKE_ARGS="-DGGML_METAL=on" pip install --no-binary=llama-cpp-python "llama-cpp-python==0.3.30"

Optional: "external" (remote API) and "server" (a running llama-server) modes
work without llama-cpp-python.

4b) FIRST-RUN SECURITY WARNING (the app is not code-signed)

---

Prebuilt binaries (.exe / .app) are not paid-signed, so the OS may warn you on
first launch. This is expected and safe. The same steps are available in-app via
the "❓ Help" button.

Windows — "Windows protected your PC":
   1. Click "More info".
   2. Click "Run anyway".
   3. Afterwards it launches normally on double-click.

macOS — "cannot verify the developer" or "app is damaged":
   Option 1 (Settings):
     1. Try to open the app (dismiss the warning).
     2. System Settings → Privacy & Security.
     3. Scroll down, find the MinecraftTranslator line, click "Open Anyway".
   Option 2 (remove quarantine in Terminal, if it says "damaged"):
     xattr -cr /Applications/MinecraftTranslator.app

   [Windows SmartScreen screenshot — placeholder]
   [macOS Privacy & Security screenshot — placeholder]

5) USING THE GUI

---

1. Select "Input folder" with your configs or modpack (config, kubejs, etc.).
2. Select "Output folder" where translated files will be written.
3. Choose target language (for example Russian (ru_ru)).
4. Press "Start" and wait until processing finishes.

The log panel will show which files were:

* translated successfully,
* skipped (already localized),
* processed with warnings,
* failed (kept untouched).

6. SUPPORTED FORMATS (SHORT)

---

* lang/en_us.json           – translated into ru_ru.json (or other locale);
* Patchouli JSON            – book pages and titles;
* FTB Quests SNBT           – quest titles, descriptions, text;
* tips/*.json               – tips and guide texts;
* KubeJS scripts            – strings in JS scripts;
* jar mods                  – automatically finds assets/*/lang/en_us.json.

Technical keys and resource locations (modid:item, namespace:path, etc.)
are intentionally not translated.

7. BUILDING APPS (SHORT)

---

Single matrix workflow builds Windows + macOS in parallel:
.github/workflows/build.yml

Built via PyInstaller from platform specs. Local inference uses a pinned
prebuilt llama-cpp-python wheel with an EXPLICIT import check (the job fails if
it does not work — no silent continue-on-error):
* Windows (x64):       MinecraftTranslator-win.spec → .exe (+ .zip)
                       artifact MinecraftTranslator-win-x64  (full, CPU)
* macOS Apple Silicon: MinecraftTranslator-mac.spec → .app in .dmg (+ .zip)
                       artifact MinecraftTranslator-mac-arm64 (full, Metal)
* macOS Intel:         MinecraftTranslator-mac.spec → .app in .dmg (+ .zip)
                       artifact MinecraftTranslator-mac-x64-external-only
                       (no macOS x86_64 prebuilt wheel → no local inference;
                        external/hybrid/server modes work)

Builds are not code-signed (see 4b about the first-run warning).

8. PROJECT STRUCTURE (MAIN PARTS)

---

minecraft_translator/
src/main.py                 (pywebview entry point)
src/gui/api.py + src/gui/web/  (bridge + HTML/CSS/JS frontend)
src/gui_main_legacy.py      (legacy Tkinter GUI, fallback)
src/processors/...
src/utils/...
src/mirrorer.py
src/detectors.py
src/translators.py
requirements.txt
secrets.json

9. LICENSE

---

MIT License.
Free to use in your modpacks, translation projects and servers.
