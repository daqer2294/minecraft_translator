"""Слэш-команды и идентификаторы в кавычках: модель их не видит, а изменённые не проходят проверку."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from src.translators import Translator, _extract_tokens
from src.utils.cache import TranslationCache
from src.utils.helpers import is_untranslatable
from src.utils.markup import has_mixed_script_word, mask, protected_code_changed, protected_code_tokens, unmask

# Настоящие записи из кэша, дословно.
CASE_25 = (
    "§lCraftPresence - Search Usage:\\n\\n §6§l/cp search <searchTerm|type:name> to search for applicable "
    "placeholders available",
    "§lCraftPresence - Поиск Использования:\\n\\n §6§l/cp поиск <searchTerm|type:name>, чтобы найти доступные "
    "для замены маркеры.",
)
CASE_38 = (
    "The endpoint URL to use for retrieving player skins\\n Notes:\\n - This endpoint is used for Scroll List "
    "Previews and the \"player.icon\" placeholder, if not overriden",
    "URL для получения кожи игрока\\n. Примечание:\\n - Этот URL используется для предпросмотров списка и замены "
    "плейсхолдера \"игрок.иконка\", если он не переопределен.",
)

# Настоящие строки сборки: что именно должно попасть в команду.
COMMANDS = [
    ("/cp reboot to reboot", ["/cp reboot"]),
    ("/cp request <accept|deny> or wait", ["/cp request"]),
    ('/cp compile "<expr>" to view', ["/cp compile"]),
    ("/craftpresence help", ["/craftpresence help"]),
    ("/give @a netherite_sword{display:x}", ["/give @a netherite_sword"]),
    ("/give command that follows", ["/give"]),
    ('Use the "/give" command', ["/give"]),
    ("Example: use /listen for other sounds", ["/listen"]),
    ("Requires operator (/op)", ["/op"]),
    ("Allows the /particle command to be used", ["/particle"]),
    ("Run /fmvariable command!", ["/fmvariable"]),
    ("Type `/cp search` here", ["/cp search"]),
    ("§6/cp search now", ["/cp search now"]),
    ("Line one\\n/cp reboot", ["/cp reboot"]),
    ("Line one\n/cp reboot", ["/cp reboot"]),
]
QUOTED = [
    ('Use the "server.icon" value', ["server.icon"]),
    ('Set "minecraft:stone" here', ["minecraft:stone"]),
    ("Read 'var.distance' first", ["var.distance"]),
    ('Set "global_var.last_rx" here', ["global_var.last_rx"]),
    ("Put it in `options.txt` now", ["options.txt"]),
    ("Ставит «player.icon» сюда", ["player.icon"]),
    ('Host "someserver.com:25565" here', ["someserver.com:25565"]),
    ('Escaped \\"player.icon\\" here', ["player.icon"]),
]
# Обычный текст: маскировать нечего, модель должна увидеть всё.
NORMAL = [
    "1/2 of the damage",
    "Takes 3/8 of a heart",
    "Hunger and/or thirst",
    "Message/Command",
    "Enable/Disable",
    "[Enable/Disable]: toggles it",
    "Show/Hide the map",
    "Speed in km/h",
    "File/Folder picker",
    "wolf/cat/parrot",
    "Files in /config/fancymenu/assets/ folder",
    "Open /usr/bin/env here",
    "Open /file.txt here",
    "Either / or",
    "/ is a slash",
    "/Help with a capital letter",
    '"Hello World" she said',
    '"Player.Icon" in capitals',
    '"e.g." is an abbreviation',
    '"Sword" of doom',
    "The player's.icon's value",
    "Don't.stop' believing",
    "Size (1.5)",
    "Damage [1.5]",
    "Sword (Diamond)",
    "Moderate (10%off)",
]


class Tokens(unittest.TestCase):
    def test_case_25_command_with_argument_inside_color_codes(self):
        self.assertEqual(protected_code_tokens(CASE_25[0]), ["/cp search"])
        self.assertIn("/cp search", mask(CASE_25[0]).tokens)
        self.assertNotIn("search <", mask(CASE_25[0]).core)
        self.assertIn("to search for applicable", mask(CASE_25[0]).core)

    def test_case_38_quoted_identifier(self):
        self.assertEqual(protected_code_tokens(CASE_38[0]), ["player.icon"])
        self.assertIn("player.icon", mask(CASE_38[0]).tokens)
        self.assertNotIn("player.icon", mask(CASE_38[0]).core)

    def test_real_commands_end_in_the_right_place(self):
        for text, tokens in COMMANDS:
            self.assertEqual(protected_code_tokens(text), tokens, text)

    def test_quoted_identifiers(self):
        for text, tokens in QUOTED:
            self.assertEqual(protected_code_tokens(text), tokens, text)

    def test_normal_strings_are_not_masked(self):
        self.assertGreaterEqual(len(NORMAL), 25)
        for text in NORMAL:
            self.assertEqual(protected_code_tokens(text), [], text)
            masked = mask(text)
            self.assertEqual(masked.tokens, [], text)
            self.assertEqual(masked.core, text.strip(), text)

    def test_urls_are_not_commands(self):
        self.assertEqual(protected_code_tokens("See https://docs.fancymenu.net/en/ here"), [])

    def test_plain_word_after_a_command_counts_as_argument(self):
        # Известная граница правила: отличить аргумент от слова текста можно только по служебным словам.
        self.assertEqual(protected_code_tokens("Use /cp reboot today"), ["/cp reboot today"])
        self.assertEqual(protected_code_tokens("Use /cp reboot one two"), ["/cp reboot one"])

    def test_translated_argument_is_not_part_of_the_command(self):
        self.assertEqual(protected_code_tokens("§6/cp поиск"), ["/cp"])

    def test_command_alone_has_nothing_to_translate(self):
        self.assertTrue(is_untranslatable("/craftpresence help"))

    def test_command_returns_after_masking(self):
        masked = mask("Use /cp search now, or wait")
        self.assertEqual(masked.tokens, ["/cp search now"])
        self.assertEqual(unmask("Используй [[0]] или жди", masked), "Используй /cp search now или жди")

    def test_quotes_may_change_around_identifier(self):
        masked = mask('Set "player.icon" here')
        answer = unmask("Укажите «[[0]]» здесь", masked)
        self.assertEqual(answer, "Укажите «player.icon» здесь")
        self.assertFalse(protected_code_changed('Set "player.icon" here', answer))
        self.assertEqual(_extract_tokens('Set "player.icon" here'), _extract_tokens(answer))


class Changed(unittest.TestCase):
    def test_both_real_cases_are_found(self):
        for source, answer in (CASE_25, CASE_38):
            self.assertTrue(protected_code_changed(source, answer), source)
            self.assertNotEqual(_extract_tokens(source), _extract_tokens(answer), source)

    def test_lost_and_extra_tokens_are_found(self):
        self.assertTrue(protected_code_changed("Run /cp reboot now", "Запустите перезагрузку"))
        self.assertTrue(protected_code_changed("Item", "Предмет /cp reboot"))
        self.assertTrue(protected_code_changed('Set "a.b" and "a.b"', 'Укажите "a.b"'))

    def test_unchanged_tokens_pass(self):
        self.assertFalse(protected_code_changed("Allows the /particle command", "Разрешает команду /particle"))
        self.assertFalse(protected_code_changed("Run /op and /give", "Запустите /give и /op"))
        self.assertFalse(protected_code_changed("Iron Ingot", "Железный слиток"))

    def test_new_token_keeps_other_checks_working(self):
        self.assertFalse(has_mixed_script_word("Запустите /cp reboot"))


class Translation(unittest.TestCase):
    class Model:
        name = "m"

        def __init__(self, answer):
            self.answer = answer
            self.seen = []

        def chat(self, messages, **kwargs):
            self.seen.append(messages[-1]["content"])
            return self.answer(messages[-1]["content"]) if callable(self.answer) else self.answer

    def _translator(self, answer):
        tr = Translator(self.Model(answer), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        tr.max_attempts = 1
        return tr

    def test_real_cases_roll_back_are_not_cached_and_go_to_second_pass(self):
        for source, answer in (CASE_25, CASE_38):
            tr = self._translator(answer)
            self.assertEqual(tr.translate(source), source)
            self.assertIsNone(tr.cache.get(source, "ru_ru"))
            self.assertEqual(tr.take_rejected(), [(source, "ru_ru")])

    def test_validation_off_still_rejects(self):
        for source, answer in (CASE_25, CASE_38):
            tr = self._translator(answer)
            tr.strict = False
            self.assertEqual(tr.translate(source), source)

    def test_model_never_sees_the_token_and_it_returns(self):
        tr = self._translator(lambda text: "Используй [[0]] сейчас")
        source = "Use /cp reboot to restart"
        self.assertEqual(tr.translate(source), "Используй /cp reboot сейчас")
        self.assertNotIn("/cp", tr.client.seen[0])

    def test_batch_item_rolls_back_and_others_stay(self):
        class Batch(self.Model):
            def chat(self, messages, **kwargs):
                user = messages[-1]["content"]
                if "JSON array" in messages[0]["content"]:
                    return json.dumps(["Запустите /cp поиск" if "[[" in x else "ПЕР " + x for x in json.loads(user)],
                                      ensure_ascii=False)
                return "Запустите /cp поиск" if "/cp" in user or "[[" in user else "ПЕР " + user

        tr = Translator(Batch(None), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        tr.max_attempts = 1
        with contextlib.redirect_stdout(io.StringIO()):
            out = tr.translate_many(["Run /cp search please to go", "Good item"])
        self.assertEqual(out, ["Run /cp search please to go", "ПЕР Good item"])
        self.assertIsNone(tr.cache.get("Run /cp search please to go", "ru_ru"))
        self.assertEqual(tr.cache.get("Good item", "ru_ru"), "ПЕР Good item")


if __name__ == "__main__":
    unittest.main()
