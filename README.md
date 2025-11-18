───────────────────────────────────────────────

# 🇷🇺 Minecraft Translator

### 🔄 Автоматический переводчик модов, квестов и конфигов Minecraft

*(FTB Quests, Patchouli, SNBT, KubeJS, lang и многое другое)*

---

## 🌍 Возможности

**Minecraft Translator** — это программа с удобным GUI, которая автоматически переводит текстовые файлы Minecraft на **любой язык**, поддерживаемый OpenAI API (например: `ru_ru`, `es_es`, `fr_fr`, `zh_cn`, `uk_ua` и др.).

Поддерживаются все ключевые форматы сборок:

* 📄 **lang/en_us.json** → ru_ru.json (или другой язык)
* 📘 **Patchouli** книги
* 🧭 **FTB Quests** (`*.snbt`) — структурный NBT-перевод
* 🧩 **Generic JSON** (tips, книги, описания, конфигурации)
* 🧙‍♂️ **KubeJS** (`*.js`) — перевод строк в коде
* 📦 **JAR-моды** — автоматическое извлечение en_us.json

---

## 🧠 Как это работает

Программа:

* 🗂 зеркально копирует структуру входной папки
* 🧵 переводит только человекочитаемые строки
* 🎨 сохраняет форматирование Minecraft (§7, §a, §e и т.д.)
* 🛡 игнорирует технические поля (id, координаты, пути ресурсов)
* 🔍 использует интеллектуальный SNBT → NBT → JSON → перевод → SNBT процесс
* ⚠ если что-то нельзя разобрать — файл остаётся целым и неизменным

---

## 🔑 Требуется API-ключ

> Для работы программы нужен ключ OpenAI API
> (или полностью совместимый провайдер по вашему выбору).

Формат файла:

```
secrets.json
```

Пример:

```json
{
  "api_key": "ВАШ_API_КЛЮЧ_ЗДЕСЬ"
}
```

---

## 🧩 Ограничения

Программа старается переводить максимально корректно, но:

* ❗ **не все строки могут переводиться идеально** — особенно длинные описания
* ❗ часть полей специально пропускается, чтобы не вызвать краши
* ❗ некоторые моды используют собственные нестандартные форматы

Однако в целом локализация получается стабильной и пригодной к использованию.

---

## 💻 Установка

1. Установите Python **3.10+** (рекомендуется 3.11).
2. Установите зависимости:

   ```
   pip install -r requirements.txt
   ```
3. Создайте файл `secrets.json` с API-ключом.
4. Запустите программу:

   ```
   python src/gui_main.py
   ```

---

## 🖥 Как пользоваться (GUI)

1. Выберите входную папку модпака
2. Выберите выходную папку
3. Укажите язык перевода (например, `ru_ru`)
4. Нажмите **Start**

Программа покажет:

* 🟩 успешно переведённые файлы
* 🟨 пропущенные или уже существующие
* 🟧 предупреждения
* 🟥 ошибки (файлы не изменены)

---

## 🛠 Сборка приложения (Windows + macOS)

Проект содержит GitHub Actions для автоматической сборки.

### 🪟 Windows (.exe)

Workflow:
`.github/workflows/build-windows.yml`

Вывод:
`MinecraftTranslator.exe`

### 🍏 macOS (binary/.app)

Workflow:
`.github/workflows/build-macos.yml`

Вывод:
`MinecraftTranslator-mac`

Артефакты появляются во вкладке **Actions** → Run → Artifacts.

---

## 📁 Структура проекта

```
minecraft_translator/
├── gui/
│   └── gui_main.py
│
├── src/
│   ├── processors/
│   │   ├── snbt_structured.py   # структурный SNBT-парсер
│   │   ├── ftb_snbt.py          # fallback парсер
│   │   ├── lang_json.py
│   │   ├── generic_json.py
│   │   ├── jar_lang.py
│   │   ├── kubejs_js.py
│   │   └── dot_lang.py
│   │
│   ├── utils/
│   │   ├── helpers.py
│   │   └── config.py
│   │
│   ├── mirrorer.py
│   ├── detectors.py
│   ├── translators.py
│   └── __init__.py
│
├── requirements.txt
├── secrets.json
└── README.md
```

---

# 🇺🇸 English Version

# 🌐 Minecraft Translator

### Automatic translation tool for Minecraft mods, quests and configs

*(FTB Quests, Patchouli, SNBT, KubeJS, lang, JSON and more)*

---

## 🌍 Features

Minecraft Translator can translate modpacks and config files into **any language supported by OpenAI**, including:

* ru_ru
* es_es
* fr_fr
* de_de
* zh_cn
* uk_ua
* and many others.

Supported formats:

* lang/en_us.json
* Patchouli books
* FTB Quests (*.snbt) — structural NBT translation
* KubeJS scripts (*.js)
* Tips / generic JSON
* en_us.json extracted from JAR mods

---

## 🔑 API Key Required

The program needs an **OpenAI API key**
(or compatible provider key).

File:

```
secrets.json
```

Example:

```json
{
  "api_key": "YOUR_API_KEY_HERE"
}
```

---

## 🧠 How It Works

* mirrors the input directory structure
* translates only meaningful human text
* preserves Minecraft formatting (§ codes)
* skips technical keys to avoid crashes
* uses SNBT → NBT → JSON → translation → SNBT pipeline
* keeps files intact on any parsing error

---

## ⚠ Limitations

* some translations may be imperfect
* technical fields are intentionally skipped
* certain mods with custom formats may only partially translate

---

## 💻 Installation

```
pip install -r requirements.txt
python src/gui_main.py
```

Create `secrets.json` before use.

---

## 🛠 Build (Windows + macOS)

Windows workflow:

```
.github/workflows/build-windows.yml
```

macOS workflow:

```
.github/workflows/build-macos.yml
```

Artifacts appear in GitHub Actions.

---

