# Minecraft Translator

Автоматический перевод модпаков Minecraft — **офлайн, локальной моделью, без ChatGPT и без API-ключей.**
*Offline Minecraft modpack translation with a local model — no ChatGPT, no API keys.*

[![Build](https://github.com/daqer2294/minecraft_translator/actions/workflows/build.yml/badge.svg)](https://github.com/daqer2294/minecraft_translator/actions/workflows/build.yml)

**Язык / Language:** [Русский](#русский) · [English](#english)

<!-- TODO: добавить скриншот -->
![screenshot](docs/screenshot.png)

---

## Русский

### Что это

Minecraft Translator — приложение с простым интерфейсом, которое автоматически
переводит тексты модпаков (названия предметов, описания, квесты, книги,
подсказки) на нужный язык.

Главная фишка: перевод может работать **полностью офлайн** — локальной моделью
прямо на вашем компьютере, без интернета, без ChatGPT и без оплаты за API. При
желании можно подключить внешний API, чтобы качественнее переводить сложные
тексты.

### Ключевые возможности

- 🔌 **Офлайн-перевод локальной моделью** (GGUF через llama.cpp) — без интернета и без ключей.
- 🧠 **Автоопределение железа** — приложение само смотрит на ваш CPU / GPU / ОЗУ и подбирает подходящую модель: лёгкую для обычных ПК, мощную для сильных машин и Apple Silicon (Metal).
- 📦 **Понимает форматы модпаков** — `lang/en_us.json` (в том числе внутри `.jar`-модов), FTB Quests (`.snbt`), Patchouli-книги, KubeJS-скрипты, tips и разные JSON-файлы.
- 🎨 **Бережно относится к разметке Minecraft** — сохраняет цветовые коды (`§a`, `§7`) и плейсхолдеры (`%s`, `{count}`), не трогает технические id и пути ресурсов (`modid:item`).
- 🔁 **Консистентность** — встроенная память переводов (кэш): одинаковые строки переводятся одинаково в разных файлах и между запусками.
- 🔀 **Гибридный режим** — массовые простые строки переводит локальная модель, а сложный лор и квесты можно догонять через внешний OpenAI-совместимый API (OpenAI, DeepSeek, Qwen и т.п.).

### Скачать

Готовые сборки — на странице релизов:

➡️ **[Последний релиз](https://github.com/daqer2294/minecraft_translator/releases/latest)**

| Платформа | Файл | Локальный перевод |
|---|---|---|
| Windows 10/11 (x64) | `MinecraftTranslator-win-x64.exe` / `.zip` | ✅ есть (CPU) |
| macOS Apple Silicon (M1–M4) | `MinecraftTranslator-mac-arm64.dmg` / `.zip` | ✅ есть (Metal) |
| macOS Intel (x86_64) | `MinecraftTranslator-mac-x64-external-only.dmg` / `.zip` | ⚠️ нет — только external / hybrid |

> **Про Intel Mac.** Для macOS на процессорах Intel готовой сборки локального
> движка нет — под macOS x86_64 отсутствует prebuilt-колесо `llama-cpp-python`
> (подробности в [`requirements-mac.txt`](requirements-mac.txt)). Поэтому такая
> сборка помечена `external-only` и работает только в режимах **external** и
> **hybrid** (через внешний API). Локальный офлайн-перевод на Intel Mac возможен
> лишь при ручной сборке из исходников (см. «Технические детали»).

### Первый запуск: предупреждение безопасности

Сборки не имеют платной цифровой подписи, поэтому при первом запуске система
может показать предупреждение. Это нормально и безопасно. Те же шаги доступны
внутри приложения — кнопка **«❓ Помощь»**.

**Windows — «Система Windows защитила ваш компьютер»:**
1. Нажмите **«Подробнее»**.
2. Нажмите появившуюся кнопку **«Выполнить в любом случае»**.
3. Дальше приложение открывается обычным двойным щелчком.

**macOS — «Не удаётся проверить разработчика» или «Приложение повреждено»:**
- *Через настройки:* попробуйте открыть приложение → закройте предупреждение →
  **Системные настройки → Конфиденциальность и безопасность** → пролистайте вниз,
  найдите строку про MinecraftTranslator → **«Всё равно открыть»**.
- *Если пишет «повреждено»* — снимите «карантин» в Терминале (подставьте свой путь):
xattr -cr /Applications/MinecraftTranslator.app

<!-- TODO: добавить скриншоты предупреждений (Windows SmartScreen, macOS «Конфиденциальность и безопасность») -->
### Как пользоваться
1. **Скачайте и откройте** приложение (см. раздел про предупреждение выше).
2. **Выберите режим.** По умолчанию — **local** (офлайн, ничего вводить не нужно).
 Для **external** / **hybrid** укажите API-ключ прямо в приложении.
3. **Первый запуск:** приложение проверит железо и предложит скачать модель.
 Дождитесь окончания загрузки — это разово (модель весит примерно 1–5 ГБ).
4. **Укажите входную папку** — папку модпака или его части (`mods`, `config`,
 `kubejs`, `assets` и т.п.).
5. **Укажите папку вывода** — куда сложить переведённые файлы (получится готовая
 зеркальная структура / ресурс-пак).
6. **Выберите язык** перевода (например, Russian `ru_ru`).
7. Нажмите **«Старт»** и дождитесь окончания. В логе видно, что переведено, что
 пропущено и где были предупреждения.
8. Возьмите результат из папки вывода и подключите к игре (как ресурс-пак или
 разложив по соответствующим папкам сборки).
> 💡 Совет: перед полным прогоном можно включить **«Проверка без записи
> (dry-run)»** — приложение прогонит перевод, ничего не записывая, чтобы вы
> оценили объём.
### Режимы работы
| Режим | Что делает | Нужен ключ / интернет |
|---|---|---|
| **local** (по умолчанию) | Всё переводит локальная модель на вашем ПК | Нет |
| **external** | Всё переводит внешний OpenAI-совместимый API | Да |
| **hybrid** | Простые строки — локально, сложные — через внешний API | Да (для сложных) |
- **local** — приватно и бесплатно; качество зависит от вашего железа и выбранной модели.
- **external** — качество внешней модели, но нужны ключ, интернет и оплата провайдера.
- **hybrid** — компромисс: экономит запросы к API, отправляя туда только сложный лор и квесты.
### Известные ограничения
- **Intel Mac** — без локального перевода (только external / hybrid), см. раздел «Скачать».
- **Первый запуск дольше** — один раз нужно скачать модель (~1–5 ГБ в зависимости от тира и вашего железа).
- **Качество на сложных текстах** — лёгкая модель (для слабых ПК) может хуже справляться с длинным лором и квестами. Для таких текстов лучше подходит тир **standard** или режим **hybrid**.
- **Перевод не идеален** — часть строк может быть неточной; некоторые технические
строки намеренно пропускаются, чтобы не сломать моды и не вызвать краши; тексты,
которые мод генерирует кодом, могут не попасть в перевод. Иногда результат
полезно слегка поправить руками.
### Сборка из исходников (для разработчиков)
Нужен Python 3.10+.
pip install -r requirements.txt
python src/main.py

Старый Tkinter-интерфейс остаётся как запасной вариант:
python -m src.gui_main_legacy

Готовые сборки собираются в GitHub Actions (Windows и macOS параллельно) —
[`.github/workflows/build.yml`](.github/workflows/build.yml). Локальная сборка:
pyinstaller --noconfirm --clean MinecraftTranslator-win.spec # Windows
pyinstaller --noconfirm --clean MinecraftTranslator-mac.spec # macOS

<details>
<summary><b>Технические детали (локальный инференс, платформенные колёса, флаги сборки)</b></summary>
**GUI-бэкенд:** pywebview. Windows — Microsoft Edge **WebView2 Runtime** (обычно
уже установлен в Windows 10/11; на старых системах — «Evergreen WebView2
Runtime»). macOS / Linux используют системный WebView (WKWebView / GTK) —
дополнительная установка не нужна.
**Локальный инференс (`llama-cpp-python`)** ставится отдельно. По умолчанию —
готовые prebuilt-колёса (без Xcode/CMake). Версия **закреплена намеренно**:
`0.3.30` — целое колесо; `0.3.32` на индексе оказалось битым (Bad CRC-32).
macOS Apple Silicon (Metal):
pip install -r requirements-mac.txt

Windows x64 (CPU):
pip install -r requirements-win.txt

Windows NVIDIA CUDA (подставьте версию CUDA, напр. cu124):
pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 "llama-cpp-python==0.3.30"

Intel Mac (x86_64) — prebuilt-колеса нет, только сборка из исходников
(нужны Xcode Command Line Tools):
CMAKE_ARGS="-DGGML_METAL=on" pip install --no-binary=llama-cpp-python "llama-cpp-python==0.3.30"

Режимы **external** (внешний API) и **server** (запущенный рядом `llama-server`)
работают без `llama-cpp-python`.
**CI-сборка** использует пиннед prebuilt-колесо с **явной проверкой**
`import llama_cpp` — job падает, если движок не импортируется (без тихого
`continue-on-error`). Для Intel Mac колеса нет, поэтому его артефакт помечается
суффиксом `-external-only`. Сборки **без code signing** — отсюда предупреждение
при первом запуске.
**Ключ для external / hybrid** хранится в `secrets.json` в корне проекта в виде
`{"OPENAI_API_KEY": "ваш_ключ"}` — либо задаётся прямо в приложении. Для режима
**local** ключ не нужен.
**Структура проекта (кратко):**
src/main.py — точка входа (pywebview GUI)
src/gui/ — Api-мост + web/ (HTML/CSS/JS фронтенд)
src/gui_main_legacy.py — прежний Tkinter GUI (fallback)
src/llm/ — провайдеры (local llama.cpp / OpenAI-совместимый),
реестр моделей, проба железа, загрузчик моделей
src/processors/ — обработчики форматов (SNBT, lang, Patchouli, KubeJS…)
src/mirrorer.py — обход файлов и выбор нужного процессора
src/translators.py — перевод, кэш, роутинг простые/сложные, ретраи

</details>
### Лицензия
Проект указан как **MIT**. Отдельного файла `LICENSE` в репозитории пока нет.
<!-- TODO: добавить файл LICENSE (по согласованию с автором — не добавлен автоматически) -->
Проект создаёт и развивает Kirill.
---
## English
### What it is
Minecraft Translator is a simple GUI app that automatically translates modpack
text (item names, descriptions, quests, books, tips) into your target language.
The key point: translation can run **fully offline** — with a local model right
on your computer, no internet, no ChatGPT, no paid API. You can optionally plug
in an external API for higher-quality translation of complex text.
### Key features
- 🔌 **Offline translation with a local model** (GGUF via llama.cpp) — no internet, no keys.
- 🧠 **Automatic hardware detection** — the app checks your CPU / GPU / RAM and picks a suitable model: a light one for regular PCs, a stronger one for powerful machines and Apple Silicon (Metal).
- 📦 **Understands modpack formats** — `lang/en_us.json` (including inside `.jar` mods), FTB Quests (`.snbt`), Patchouli books, KubeJS scripts, tips and various JSON files.
- 🎨 **Respects Minecraft markup** — keeps color codes (`§a`, `§7`) and placeholders (`%s`, `{count}`), and leaves technical ids and resource paths (`modid:item`) untouched.
- 🔁 **Consistency** — a built-in translation memory (cache): identical strings translate the same way across files and runs.
- 🔀 **Hybrid mode** — bulk simple strings go through the local model, while hard lore and quests can be handled by an external OpenAI-compatible API (OpenAI, DeepSeek, Qwen, etc.).
### Download
Prebuilt binaries are on the releases page:
➡️ **[Latest release](https://github.com/daqer2294/minecraft_translator/releases/latest)**
| Platform | File | Local translation |
|---|---|---|
| Windows 10/11 (x64) | `MinecraftTranslator-win-x64.exe` / `.zip` | ✅ yes (CPU) |
| macOS Apple Silicon (M1–M4) | `MinecraftTranslator-mac-arm64.dmg` / `.zip` | ✅ yes (Metal) |
| macOS Intel (x86_64) | `MinecraftTranslator-mac-x64-external-only.dmg` / `.zip` | ⚠️ no — external / hybrid only |
> **About Intel Mac.** There is no prebuilt local engine for Intel macOS — no
> `llama-cpp-python` prebuilt wheel exists for macOS x86_64 (see
> [`requirements-mac.txt`](requirements-mac.txt)). That build is therefore marked
> `external-only` and works only in **external** and **hybrid** modes (via an
> external API). Local offline translation on Intel Mac is possible only by
> building from source (see “Technical details”).
### First launch: security warning
The builds are not paid-signed, so on first launch the OS may show a warning.
This is expected and safe. The same steps are available in-app via the
**“❓ Help”** button.
**Windows — “Windows protected your PC”:**
1. Click **“More info”**.
2. Click **“Run anyway”**.
3. Afterwards it launches normally on double-click.
**macOS — “cannot verify the developer” or “app is damaged”:**
- *Via Settings:* try to open the app → dismiss the warning →
  **System Settings → Privacy & Security** → scroll down, find the
  MinecraftTranslator line → **“Open Anyway”**.
- *If it says “damaged”* — remove quarantine in Terminal (use your real path):
xattr -cr /Applications/MinecraftTranslator.app

<!-- TODO: add screenshots (Windows SmartScreen, macOS Privacy & Security) -->
### How to use
1. **Download and open** the app (see the warning section above).
2. **Choose a mode.** Default is **local** (offline, nothing to enter). For
 **external** / **hybrid**, set your API key in the app.
3. **First launch:** the app checks your hardware and offers to download a model.
 Wait for it to finish — this is a one-time step (the model is roughly 1–5 GB).
4. **Pick the input folder** — your modpack or a part of it (`mods`, `config`,
 `kubejs`, `assets`, etc.).
5. **Pick the output folder** — where translated files go (you get a mirrored
 structure / resource pack).
6. **Choose the target language** (for example Russian `ru_ru`).
7. Press **“Start”** and wait. The log shows what was translated, skipped, or had
 warnings.
8. Take the result from the output folder and add it to the game (as a resource
 pack or by placing files into the matching modpack folders).
> 💡 Tip: before a full run you can enable **“dry-run”** — the app runs the
> translation without writing files so you can gauge the volume.
### Modes
| Mode | What it does | Key / internet needed |
|---|---|---|
| **local** (default) | A local model on your PC translates everything | No |
| **external** | An external OpenAI-compatible API translates everything | Yes |
| **hybrid** | Simple strings locally, hard ones via the external API | Yes (for hard ones) |
- **local** — private and free; quality depends on your hardware and the model.
- **external** — external-model quality, but needs a key, internet and provider billing.
- **hybrid** — a compromise: saves API calls by sending only hard lore and quests.
### Known limitations
- **Intel Mac** — no local translation (external / hybrid only), see “Download”.
- **Slower first launch** — you download a model once (~1–5 GB depending on tier and hardware).
- **Quality on complex text** — the light model (for weaker PCs) can struggle with long lore and quests; for those, the **standard** tier or **hybrid** mode works better.
- **Not perfect** — some lines may be rough; some technical strings are skipped on
purpose to avoid breaking mods; text a mod generates in code may not be picked
up. Manual polishing of some translations is sometimes worth it.
### Building from source (for developers)
Requires Python 3.10+.
pip install -r requirements.txt
python src/main.py

The legacy Tkinter UI remains as a fallback:
python -m src.gui_main_legacy

Prebuilt binaries are produced by GitHub Actions (Windows and macOS in parallel) —
[`.github/workflows/build.yml`](.github/workflows/build.yml). Local build:
pyinstaller --noconfirm --clean MinecraftTranslator-win.spec # Windows
pyinstaller --noconfirm --clean MinecraftTranslator-mac.spec # macOS

<details>
<summary><b>Technical details (local inference, platform wheels, build flags)</b></summary>
**GUI backend:** pywebview. Windows uses the Microsoft Edge **WebView2 Runtime**
(usually preinstalled on Windows 10/11; on older systems install the “Evergreen
WebView2 Runtime”). macOS / Linux use the system WebView (WKWebView / GTK) — no
extra install.
**Local inference (`llama-cpp-python`)** is installed separately. By default it
uses prebuilt wheels (no Xcode/CMake). The version is **pinned on purpose**:
`0.3.30` is intact; `0.3.32` on the index was corrupted (Bad CRC-32).
macOS Apple Silicon (Metal):
pip install -r requirements-mac.txt

Windows x64 (CPU):
pip install -r requirements-win.txt

Windows NVIDIA CUDA (set your CUDA version, e.g. cu124):
pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 "llama-cpp-python==0.3.30"

Intel Mac (x86_64) — no prebuilt wheel, build from source
(needs Xcode Command Line Tools):
CMAKE_ARGS="-DGGML_METAL=on" pip install --no-binary=llama-cpp-python "llama-cpp-python==0.3.30"

The **external** (remote API) and **server** (a running `llama-server`) modes
work without `llama-cpp-python`.
**CI build** installs the pinned prebuilt wheel and does an **explicit**
`import llama_cpp` check — the job fails if the engine can’t import (no silent
`continue-on-error`). Intel Mac has no wheel, so its artifact is suffixed
`-external-only`. Builds are **not code-signed**, hence the first-run warning.
**Key for external / hybrid** lives in `secrets.json` at the project root as
`{"OPENAI_API_KEY": "your_key"}`, or is set directly in the app. The **local**
mode needs no key.
**Project layout (short):**
src/main.py — entry point (pywebview GUI)
src/gui/ — Api bridge + web/ (HTML/CSS/JS frontend)
src/gui_main_legacy.py — legacy Tkinter GUI (fallback)
src/llm/ — providers (local llama.cpp / OpenAI-compatible),
model registry, hardware probe, model downloader
src/processors/ — format handlers (SNBT, lang, Patchouli, KubeJS…)
src/mirrorer.py — file walk and processor selection
src/translators.py — translation, cache, simple/complex routing, retries

</details>
### License
The project is stated as **MIT**. There is no dedicated `LICENSE` file in the
repository yet.
<!-- TODO: add a LICENSE file (to be confirmed with the author — not added automatically) -->
Created and maintained by Kirill.
