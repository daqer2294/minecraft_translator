# Форматы текста в сборках NeoForge: что есть и что поддерживает код

Дата проверки всех источников: **2026-11-04**.
Код сверялся на коммите `53a8d77`.

Уровни доверия к фактам:

- **[ДИСК]** — проверено на реальных файлах сборок на этой машине. Самый сильный уровень.
- **[КОД]** — прочитан исходник мода или загрузчика.
- **[ДОК]** — официальная документация или changelog.
- **[СООБЩ]** — вики сообщества или сторонний гайд. Использовать только вместе с другими подтверждениями.

Источники данных [ДИСК]:

| Сборка | MC | Загрузчик | Jar | Метаданные на диске |
|---|---|---|---|---|
| All the Mods 10 (ATM10), v8.4 | 1.21.1 | NeoForge 21.1.265 | 495 | `manifest.json` и `minecraftinstance.json` |
| Create Stranded at Sea | 1.21.1 | NeoForge 21.1.218 | 160 | только `minecraftinstance.json` |
| Cobblemon SMP (Complex Gaming Cobblemon v1.7.1) | 1.21.1 | **Fabric 0.18.1, не NeoForge** | 71 | оба файла |
| Calamycraft | 1.20.1 | Forge 47.4.0 | 354 | оба файла |

## Сводная таблица поддержки

| Формат | Где живёт (1.21.x) | Наш код | Суть проблемы |
|---|---|---|---|
| Lang в верхнем jar | `assets/<ns>/lang/en_us.json` | **Частично** | Находит, но перезаписывает уже готовый `ru_ru`, шлёт по одному запросу на строку |
| Lang во вложенном jar | `META-INF/jarjar/*.jar` | **Нет** | ATM10: 32 вложенных jar с lang, Stranded: 6 |
| Встроенные паки в jar | `resourcepacks/*`, `packs/*` внутри jar | **Нет** | ATM10: 3, Cobblemon: 1 |
| Версия MC и загрузчик | `manifest.json`, `minecraftinstance.json` | **Нет** | Не читаются вообще |
| Готовый ресурспак | `pack.mcmeta` + zip | **Нет** | Вывод `jar_lang/<jar>/assets/...` игра не загрузит |
| FTB Quests: lang | `config/ftbquests/quests/lang/...` | **Сломано** | Пишет в `en_us`, «переводит» все чужие локали |
| FTB Quests: главы | `quests/chapters/*.snbt` | **Опасно** | Текста там нет, а hex-ID уходят в LLM и пишутся обратно |
| Heracles | JSON в `heracles/quests` | **Нет** | В 4 сборках на диске не встречается, приоритет низкий |
| Patchouli в jar | `assets/<ns>/patchouli_books/<book>/en_us/` | **Нет** | ATM10: 1539 файлов, 739 тыс. символов текста без перевода |
| Patchouli вне jar | то же в `kubejs/assets` или паке | **Частично** | Нет защиты макросов `$(...)` |
| `book.json` Patchouli | `data/<ns>/patchouli_books/<book>/book.json` | **Нет** | `name`, `landing_text` |
| KubeJS lang | `kubejs/assets/<ns>/lang/en_us.json` | **Частично** | Падает на UTF-8 BOM, главный файл ATM10 не читается |
| KubeJS `.js` | `kubejs/**/*.js` | **Частично, рискованно** | Копирует все скрипты в вывод, не видит `.displayName()` |
| `ClientEvents.lang` | `client_scripts` | **Нет** | В наших сборках 0 скриптов, приоритет низкий |
| GuideME | `assets/<ns>/<guide>/*.md`, перевод в `_ru_ru/` | **Нет** | ATM10: 643 файла, 1,4 МБ |
| Oracle Index | `assets/oracle_index/books/**` | **Нет** | ATM10: 560 файлов, Stranded: 120 |
| Руководство Immersive Engineering | `assets/<ns>/manual/en_us/*.txt` | **Нет** | ATM10: 114 файлов, 124 КБ |
| Modonomicon | `data/<ns>/modonomicon/**` | Не нужно | В просмотренном примере текст идёт ключами через lang |
| Ponder (Create) | ключи в lang | **Да** | 1470–1561 ключ, идут как обычный lang |
| Advancements | `data/<ns>/advancement/**` | Не нужно | 99,8 % уже `translate`-ключи |
| `custom_name`, `set_name` | `data/**/recipe`, `loot_table` | **Нет** | ATM10: 62 литерала, Stranded: 1. Приоритет низкий |
| Tips | `assets/<ns>/tips/*.json` | **Вредно** | Ключ `tip.x.text` уходит в LLM как текст и портится |
| Open Loader | `config/openloader/{resources,data}` | **Нет** | Паки в zip не сканируются |
| Paxi | `config/paxi/{resourcepacks,datapacks}` | **Нет** | В наших сборках не найден |
| Паки в `resourcepacks/`, `datapacks/` | zip | **Нет** | Cobblemon-Tips (1 en_us), MobCrates (1 en_us) |
| Сплэши | `assets/minecraft/texts/splashes.txt` | **Нет** | Ваниль их не локализует, приоритет низкий |

## 1. Метаданные мода и jar-in-jar

| Факт | Уровень | Источник |
|---|---|---|
| Единственный файл метаданных NeoForge с 20.5: `META-INF/neoforge.mods.toml` | [ДОК] | [NeoForge 20.5 release](https://neoforged.net/news/20.5release/), 2024-04-23 |
| `[[mods]]` может быть несколько в одном файле, поэтому один jar может содержать несколько модов | [ДОК] | [Mod Files](https://docs.neoforged.net/docs/gettingstarted/modfiles/), документация 26.1 |
| Реальные jar всё равно часто содержат и `mods.toml`: ATM10 — 320 `neoforge.mods.toml` и 14 `mods.toml` | [ДИСК] | ATM10 |
| Манифест вложенных jar: `META-INF/jarjar/metadata.json`, поля `identifier`, `version.range`, `version.artifactVersion`, `path` | [ДИСК] + [ДОК] | `xaerominimap-neoforge`, [Jar-in-Jar](https://docs.neoforged.net/toolchain/docs/dependencies/jarinjar) |
| Вложенные jar лежат в `META-INF/jarjar/*.jar`. Встречается и `META-INF/jars/` | [ДИСК] | `entityculling-neoforge`: `META-INF/jars/TRender-*.jar` |
| FML читает вложенный jar как отдельный мод (`pipeline.readModFile`) и выбирает одну версию через `JarSelector` | [КОД] | [`JarInJarDependencyLocator`](https://github.com/neoforged/FancyModLoader/blob/main/loader/src/main/java/net/neoforged/fml/loading/moddiscovery/locators/JarInJarDependencyLocator.java), ветка main |
| Все вложенные jar с lang на диске имеют свой `mods.toml`: ATM10 32/32, Stranded 6/6 | [ДИСК] | Сканирование |

**Вывод.** Вложенные моды участвуют в игре как обычные моды. Их `lang` нужно обрабатывать, но версию брать одну: выигрывает версия, которую выбирает `JarSelector`, поэтому один и тот же мод может лежать в нескольких jar.

Что было не прочитано: точный алгоритм выбора версии. Для перевода это не критично, ключи пакуются в общий ресурспак.

| Что | Наш код | Не хватает |
|---|---|---|
| `neoforge.mods.toml` | Не читается | Для отчёта и контекста можно брать `modId`, `displayName` |
| `META-INF/jarjar/*.jar` | Не заходит внутрь | Рекурсивное чтение. Добавит 1133 строки ATM10, 244 Stranded, 717 Cobblemon |
| Встроенные паки в jar | Не видит | `resourcepacks/*/assets/*/lang`, `packs/*/assets/*/lang` |

## 2. Где лежит lang в 1.21.x

| Факт | Уровень | Источник |
|---|---|---|
| Путь: `assets/<modid>/lang/<locale>.json`, JSON `ключ → текст` | [ДОК] | [NeoForge i18n](https://docs.neoforged.net/docs/resources/client/i18n/), документация 26.1 |
| При отсутствии ключа игра берёт `en_us`. Если нет и там, показывается сам ключ | [ДОК] | Там же |
| Ключи из разных паков сливаются **поштучно**, а не целым файлом | [ДОК] | [Resource pack](https://minecraft.wiki/w/Resource_pack) |
| Код русского: `ru_ru` | [ДОК] + [ДИСК] | [Language](https://minecraft.wiki/w/Language), реальные файлы |
| Устаревшие `.lang` встречаются до сих пор: `en_us.lang`, `en_US.lang` | [ДИСК] | `mahoutsukai`, `reforgedplaymod` |
| UTF-8 BOM в lang-файлах встречается | [ДИСК] | `sodiumleafculling`, `kubejs/assets/kubejs/lang/en_us.json` в ATM10 |
| Нестандартные пути: `data/<ns>/lang/en_us.json`, `ae2ct/lang/en_us.json` | [ДИСК] | `open-parties-and-claims`, `ae2ct` |

**Можно ли переводить частично: да.** Это прямое следствие поштучного слияния и fallback: ресурспак может содержать только недостающие ключи.

**Важное следствие слияния.** Пак с полным `ru_ru.json` перекроет официальный перевод мода. Поэтому в пак нужно класть **только** те ключи, которых нет в официальном `ru_ru`.

Покрытие официальным русским, [ДИСК]:

| Сборка | Всего строк | Уже есть `ru_ru` | Остаётся |
|---|---:|---:|---:|
| Cobblemon SMP | 17 781 | 13 343 (75 %) | 4 141 |
| Stranded | 32 297 | 18 953 (59 %) | 13 121 |
| ATM10 | 162 977 | 86 085 (53 %) | 76 146 |

Наш код сейчас: `process_jar_lang` не смотрит на `ru_ru` и переводит всё заново. Подтверждено тестом H4 в отчёте.

## 3. CurseForge: версия MC и загрузчик

Официальной спецификации этих файлов я не нашёл, адрес `docs.curseforge.com/docs/` вернул 404. Всё ниже — по реальным файлам.

| Файл | Поля | Встречаемость на диске |
|---|---|---|
| `manifest.json` | `minecraft.version`, `minecraft.modLoaders[].id` (`neoforge-21.1.265`, `fabric-0.18.1`, `forge-47.4.0`), `primary`, `name`, `version`, `overrides` | 3 из 4. В Stranded файла нет |
| `minecraftinstance.json` | `gameVersion` (`1.21.1`), `baseModLoader.name` (`neoforge-21.1.218`, `fabric-0.18.1-1.21.1`), `installedAddons` | 4 из 4 после завершения установки |

**Важно:** нельзя полагаться на один файл. `minecraftinstance.json` появляется только после установки: у ATM10 он создался, когда докачка завершилась. У Stranded нет `manifest.json`. Порядок чтения: `minecraftinstance.json`, затем `manifest.json`, затем запасной вариант: определить по `META-INF/neoforge.mods.toml` и `fabric.mod.json` в модах.

Также имя загрузчика в `baseModLoader.name` может содержать версию MC (`fabric-0.18.1-1.21.1`), а `forgeVersion` у Fabric содержит версию Fabric. Парсить нужно префикс имени.

Наш код: не читает ни один из файлов.

## 4. Формат ресурспака и `pack_format`

Таблица для релизов, [ДОК] [Pack format](https://minecraft.wiki/w/Pack_format):

| Java Edition | Resource pack | Data pack |
|---|---:|---:|
| 1.21 – 1.21.1 | 34 | 48 |
| 1.21.2 – 1.21.3 | 42 | 57 |
| 1.21.4 | 46 | 61 |
| 1.21.5 | 55 | 71 |
| 1.21.6 | 63 | 80 |
| 1.21.7 – 1.21.8 | 64 | 81 |
| 1.21.9 – 1.21.10 | 69.0 | 88.0 |
| 1.21.11 | 75.0 | 94.1 |
| 26.1 – 26.1.2 | 84.0 | 101.1 |
| 26.2 | 88.0 | 107.1 |
| 26.3 | 97.1 | 121.0 |

Значение 34 для 1.21.1 подтверждено и на диске: `version.json` в клиентском `1.21.1.jar` содержит `pack_version.resource = 34` [ДИСК].

**Заменили ли `pack_format` на `min_format` / `max_format`: да, с 25w31a.** С этой же версии номер формата имеет дробную часть. [ДОК] [pack.mcmeta](https://minecraft.wiki/w/Pack.mcmeta):

- Начиная с 1.21.9 `min_format` и `max_format` обязательны. Значение: целое или `[major, minor]`.
- `pack_format` необязателен, но нужен, если пак поддерживает старые версии (resource < 65, data < 82).
- По `supported_formats` вики противоречит сама себе: раздел истории говорит «удалён в 1.21.9», раздел формата описывает его как поле совместимости. **Проверить запуском перед тем, как писать пак для 1.21.9+.**

Шаблон `pack.mcmeta`:

```json
{ "pack": { "pack_format": 34, "description": "Russian translation" } }
```

Для 1.21.9 и новее:

```json
{ "pack": { "min_format": 69, "max_format": 69, "description": "Russian translation" } }
```

Наш код: `pack.mcmeta` не создаётся, вывод не является ресурспаком. Подтверждено тестом H4.

## 5. FTB Quests

| Факт | Уровень | Источник |
|---|---|---|
| С 2100.1.0 (MC 1.21) текст вынесен из данных квестов в `lang/`, по файлу на локаль (`lang/en_us.snbt`) | [ДОК] | [CHANGELOG FTB Quests](https://github.com/FTBTeam/FTB-Quests/blob/main/CHANGELOG.md) |
| При загрузке старой книги создаётся `lang/en_us.snbt` автоматически (2100.1.1) | [ДОК] | Там же |
| Fallback-локаль настраивается в свойствах книги (2101.1.13) | [ДОК] | Там же |
| Unicode-escape в файлах перевода требует двойного экранирования: `\\u2022` (2101.1.3) | [ДОК] | Там же |
| В 26.1.2.1 перевод разбит на файлы по типу объекта и главе, а сами данные квестов больше не SNBT. Формат lang-файлов в этой версии **не проверен** | [ДОК] | Там же |
| Stranded (FTB Quests 2101.1.21): один `lang/en_us.snbt`, 1669 ключей, 2478 строк | [ДИСК] | Сборка |
| ATM10 (2101.1.37 + мод `ftbquestslangsplitter`): `lang/<locale>/{chapter,chapter_group,file,reward_table}.snbt` и `chapters/*.snbt`, 15 локалей | [ДИСК] | Сборка |
| ATM10 уже содержит `lang/ru_ru/`: 7489 ключей, из них 6532 реально отличаются от `en_us` при 9222 ключах `en_us` | [ДИСК] | Сборка |
| В главах текста нет: поля `title`/`description`/`subtitle` есть в 0 из 25 глав Stranded и 1 из 67 глав ATM10 | [ДИСК] | Сборка |
| Файл для русского: **`ru_ru.snbt` или `ru_ru/` в нижнем регистре** | [ДИСК] | Папка `ru_ru` в ATM10 |

**Противоречие в источниках.** [Гайд ATM](https://allthemods.github.io/alltheguides/guides/translations) приводит код `ru_RU`, но в самой ATM10 папка называется `ru_ru`. Верю диску.

Формат значений: плоский SNBT, значение — строка или список строк:

```text
quest.007A11C49E06F15F.title: "&9Crafting Components Blueprints "
quest.007A11C49E06F15F.quest_desc: ["These can be Crafted. \\n\\nThe &9Blueprint&r ..."]
```

Разметка на диске, ATM10, 11 285 строк: `&`-коды в 5348, `\\n` в 2327, JSON-компоненты в 760, `{@pagebreak}` в 90, плюс `{image:ns:path width:100 ...}`.

Объём текста квестов в `en_us` ATM10: 1,24 млн символов. Для сравнения: уникальные lang-строки без перевода занимают 2,09 млн символов, но значительная их часть — шаблонные названия блоков (`Seat`, `Back`, цвета, породы дерева).

| Что | Наш код | Проблема |
|---|---|---|
| `lang/en_us.snbt` | Переводит и пишет в **`en_us.snbt`** | Игра на русском его не прочитает как `ru_ru`. Перевод попадает «не туда» |
| `lang/<другая локаль>/` | Обрабатывает все 15 локалей | ATM10: 41 348 лишних запросов. `pt_br` идёт в LLM и записывается по пути `pt_br` |
| `lang/ru_ru/` | Отправляет готовый русский в LLM | ATM10: 2250 лишних запросов на строки со смешанным текстом |
| `chapters/*.snbt` | Шлёт ID зависимостей в LLM | Ключ `dependencies` не в списке технических. Глава `bosses.snbt`: 14 hex-ID ушли в LLM, 7 ID перезаписаны ответом. Изменённый моделью ID сломает связь квестов |
| Валидатор токенов | `{@pagebreak}` и `&`-коды не считает | Модель может потерять их незаметно |

## 6. Другие квест-моды

| Мод | Хранение текста | Уровень | На диске |
|---|---|---|---|
| Heracles (ветка 1.21.1, Fabric и NeoForge, обновлён 2026-10-18) | JSON-файлы в `<путь>/heracles/quests/<группа>/<id>.json`, поля `title`, `subtitle`, `description` прямо в файле. Базовый `<путь>` передаётся параметром, что именно он означает, я не проверил | [КОД] [QuestHandler](https://github.com/terrarium-earth/Heracles/blob/1.21.1/common/src/main/java/earth/terrarium/heracles/common/handlers/quests/QuestHandler.java), [QuestDisplay](https://github.com/terrarium-earth/Heracles/blob/1.21.1/common/src/main/java/earth/terrarium/heracles/api/quests/QuestDisplay.java) | Ни в одной из 4 сборок |
| Прочие квест-моды | Поиск по именам jar не нашёл других квестовых модов, кроме FTB Quests (`merequester` — это не квесты) | [ДИСК] | Нет |

Поиск в вебе по Heracles и Odyssey Quests вернул нерелевантные результаты. Ответа, насколько часто Heracles используется в NeoForge-сборках, у меня нет. В выборке из 4 сборок — ни разу.

## 7. Patchouli

| Факт | Уровень | Источник |
|---|---|---|
| С 1.20 `book.json` лежит в `data/`, содержимое в `assets/<ns>/patchouli_books/<book>/<locale>/{categories,entries,templates}`, нужен `use_resource_pack: true` | [ДОК] | [Upgrade guide 1.20](https://vazkiimods.github.io/Patchouli/docs/upgrading/upgrade-guide-120) |
| Внешняя папка `.minecraft/patchouli_books/<book>/` всё ещё поддерживается | [ДОК] | Там же |
| Fallback: ищется файл текущей локали (`en_us` заменяется в пути на текущий язык), при отсутствии берётся `en_us` | [КОД] | [`BookContentsBuilder.loadLocalizedJson`](https://github.com/VazkiiMods/Patchouli/blob/26.1/Xplat/src/main/java/vazkii/patchouli/client/book/BookContentsBuilder.java), ветка 26.1 |
| Список записей определяется по `en_us`, а перевод только заменяет содержимое | [КОД] | Там же, `load()` |
| `i18n: true` означает, что заголовки и текст страниц ищутся как ключи в lang | [ДОК] | [book.json](https://vazkiimods.github.io/Patchouli/docs/reference/book-json) |
| Макросы `$(br)`, `$(br2)`, `$(l:id)`, `$(item)`, `$()`, `$(#hex)` | [ДОК] | [Text formatting](https://vazkiimods.github.io/Patchouli/docs/patchouli-basics/text-formatting) |
| ATM10: 1539 файлов `en_us`, 216 с готовым `ru_ru`; 26 книг, все `use_resource_pack`, 3 с `i18n: true`; 5737 текстовых полей, 739 тыс. символов; `$(` в 2246 полях | [ДИСК] | Сборка |
| Stranded: 83 файла, 232 поля, 31 тыс. символов | [ДИСК] | Сборка |

**Перевод можно положить в ресурспак:** `assets/<ns>/patchouli_books/<book>/ru_ru/...`. Fallback позволяет переводить файлы частично (по файлам, а не по полям).

| Что | Наш код | Не хватает |
|---|---|---|
| Patchouli внутри jar | Не видит совсем | Чтение из zip, запись в пак |
| Patchouli вне jar | `generic_json`, по одной строке | Батчи, защита `$(...)` в валидаторе |
| `book.json`: `name`, `landing_text` | Не читает | Перевод в отдельный файл невозможен, если нет `i18n`; решение надо проверять запуском |

## 8. KubeJS

| Факт | Уровень | Источник |
|---|---|---|
| `ClientEvents.lang('en_us', e => { e.add('key', 'Text'); e.renameItem(...) })`, скрипты в `client_scripts/`. `add` работает только для ключа без перевода | [ДОК] | [ClientEvents.lang](https://kubejs.com/wiki/events/ClientEvents/lang), обновлено 2025-01-07, версия KubeJS на странице не указана |
| Изменить уже переведённый ключ скриптом нельзя, только ресурспаком | [ДОК] | Там же |
| `kubejs/assets/<ns>/lang/<locale>.json` реально используется | [ДИСК] | ATM10: en_us 6, ru_ru 55 |
| `ClientEvents.lang` не встречается ни в одном скрипте | [ДИСК] | ATM10 215 js, Stranded 50 js |
| В скриптах текст бывает в `.displayName(...)`, `Text.of/green/red/...`, `tell(...)`, `Component...` | [ДИСК] | ATM10: `Text.of` 89, `.displayName` 35, `Text.green` 20 |
| `kubejs/assets/immersiveengineering/manual/ru_ru/*.txt` | [ДИСК] | ATM10 |

| Что | Наш код | Проблема |
|---|---|---|
| `kubejs/assets/*/lang/en_us.json` | Читает | Падает на BOM: `[ERR][lang] kubejs/assets/kubejs/lang/en_us.json`. Это главный файл ATM10 |
| Готовый `kubejs/.../ru_ru.json` | Не учитывает | Переведёт поверх |
| `.js` | Regex по `Text.of`, `tell`, `console.log`, `sendMessage` | `.displayName` и `Text.green` не покрыты, зато `console.log` переводится зря. Копирует 215 скриптов в вывод, даже если ничего не изменилось |

Оригиналы не перезаписываются, вывод идёт в `out_root`. Но перенос `.js` обратно в сборку перезаписал бы файлы автора.

## 9. Текст в данных и прочие места

| Где | Факт | Уровень | Наш код |
|---|---|---|---|
| Advancements | С 1.21 папка `data/<ns>/advancement/` в единственном числе ([СООБЩ] [changelog](https://modrinth.com/datapack/better-craftables/version/v1.6.0-beta4), на диске оба варианта: 1.20.1 `advancements`, 1.21.1 `advancement`). `display.title` и `description` — text component ([ДОК](https://minecraft.wiki/w/Advancement_definition)) | [ДИСК]: `translate` в ATM10 1807 из 1810, Stranded 364/364, Cobblemon 60/60 | Не нужно, всё идёт через lang |
| `custom_name`, `set_name` | Точный разбор JSON: ATM10 — 163 с `translate`, 62 литерала; Stranded — 1 литерал; Cobblemon — 0 | [ДИСК] | Нет, приоритет низкий |
| Tips | `assets/<ns>/tips/*.json`, `{"type":"tipsmod:simple","text":{"translate":"tip.x.text"}}`. Все 99 в ATM10 и все 81 в Cobblemon через ключи, литералов 0 | [ДИСК] | **Вредно:** `generic_json` отправляет сам ключ `tip.tipsmod.animal_growing.text` в LLM |
| Сплэши | `assets/minecraft/texts/splashes.txt`, UTF-8, ваниль их не локализует | [ДОК] [Language](https://minecraft.wiki/w/Language), [Resource pack](https://minecraft.wiki/w/Resource_pack) | Нет. В NeoForge-сборках на диске не переопределены |
| Open Loader | `config/openloader/resources` и `data`, zip или папка, нужен `pack.mcmeta` ([СООБЩ] [Modrinth](https://modrinth.com/mod/open-loader), сверено с реальной папкой) | [ДИСК]: `Cobblemon-Tips-1.0.0.zip` содержит lang `en_us` | Нет: zip не открываются |
| Paxi | `config/paxi/datapacks`, `resourcepacks`, порядок в `datapack_load_order.json` и `resourcepack_load_order.json` | [СООБЩ] [Modrinth](https://modrinth.com/mod/paxi) | Нет. На диске не найден |
| Паки сборки | `resourcepacks/*.zip`, `datapacks/*.zip`, `global_packs/` | [ДИСК]: MobCrates содержит 1 `en_us` | Нет |
| Oracle Index | `assets/oracle_index/books/<book>/...`, 560 в ATM10, 120 в Stranded | [ДИСК]; формат по документации не проверял | Нет |
| Руководство IE | `assets/immersiveengineering/manual/en_us/*.txt`, 114 файлов | [ДИСК] | Нет |
| Konkrete | `assets/konkrete/locals/en_us.local` | [ДИСК] | Нет, единичный случай |
| GuideME | Перевод в `_ru_ru/` от корня гайда, при отсутствии показывается страница по умолчанию ([ДОК] [Translating Guides](https://guideme.appliedenergistics.org/translation)). ATM10: 643 `.md`, 1,4 МБ, папок `_ru_ru` мало | [ДИСК] | Нет |

## 10. Плейсхолдеры и разметка

| Разметка | Что это | Источник | На диске (строки без перевода) | Валидатор кода |
|---|---|---|---|---|
| `%s`, `%1$s`, `%d` | Слоты форматирования. `%%` — литерал | [Resource pack](https://minecraft.wiki/w/Resource_pack), [Text component](https://minecraft.wiki/w/Text_component_format) | ATM10 1914, Stranded 576, Cobblemon 347 | Да. `%%s` разбирается неверно |
| `{0}`, `{count}` | Слоты конкретных модов | [ДИСК] | 10 / 4 / 2 | Да |
| `§a` | Коды форматирования, работают в lang | [Formatting codes](https://minecraft.wiki/w/Formatting_codes) | 113 / 350 / 118 | **Нет** |
| `&a` | Ванилью не поддерживается, используется модами, в FTB Quests основной способ | Там же, гайд ATM | 152 в lang, 5348 в FTB | **Нет** |
| `\n` и `\\n` | Перенос. В FTB двойное экранирование | Changelog FTB, [ДИСК] | 1165 / 247 / 265 | **Нет** |
| JSON text component | В значениях FTB, в `custom_name` | [Text component](https://minecraft.wiki/w/Text_component_format). С 1.21.5 `hoverEvent` → `hover_event`, значения в SNBT | 760 в FTB ATM10 | Частично, только в SNBT-процессоре |
| `$(item)`, `$(br2)`, `$(l:...)` | Patchouli | Документация Patchouli | 2246 в Patchouli ATM10 | **Нет** |
| `{@pagebreak}`, `{image:...}` | FTB Quests | [ДИСК] | 90 + изображения | **Нет**; `{image:...}` даёт ложные токены `image:atm`, `width:100` |
| `<red>...</red>` | Теги | [ДИСК] | 9 / 71 / 68 | **Нет** |
| Ключи перевода внутри значения | `{my.translation.key}` в FTB | Changelog FTB 1902.4.3 | 0 в проверенных данных | Нет |

**Ложные срабатывания валидатора.** Регулярка `RE_NAMESPACE` считает токенами `1:2` и `Note:example`. Если модель поменяет пробелы вокруг двоеточия или переведёт слово, такая строка откатывается на английский. Цифра по долям откатов — в отчёте `docs/PHASE_A_REPORT.md`.

## Что осталось непроверенным

1. Загрузка готового ресурспака в самой игре: игру я не запускал. Поштучное слияние и fallback описаны в документации, но проверка запуском нужна в фазе B.
2. Формат FTB Quests 26.1.2.x: сборок этой версии на диске нет.
3. Heracles: базовый путь в реальной сборке и частота использования.
4. Oracle Index: формат файлов по документации.
5. Перевод `book.json` у Patchouli без `i18n`: механизм для отдельного языка я не нашёл.
