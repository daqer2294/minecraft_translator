"""Термины в запросе модели и проверка ответа по корню."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from src import config, mirrorer
from src.glossary import Glossary, Term, load_fixed_terms, roots_of
from src.translators import Translator
from src.utils.cache import TranslationCache

TERMS = [
    Term("Bulbasaur", "Бульбазавр", "pokemon"),
    Term("Heracross", "Геракросс", "pokemon"),
    Term("Mew", "Мью", "pokemon"),
    Term("Headbutt", "Удар головой", "move"),
    Term("Leech Seed", "Семя пиявки", "move"),
    Term("Bind", "Связывание", "move"),
    Term("Fly", "Полёт", "move"),
    Term("Static", "Статическое электричество", "ability"),
    Term("Fire", "Огонь", "block"),
    Term("Stone", "Камень", "block"),
    Term("Poké Ball", "Покебол", "item"),
]


def glossary():
    return Glossary(TERMS, load_fixed_terms("ru_ru"))


class Find(unittest.TestCase):
    def setUp(self):
        self.g = glossary()

    def names(self, text):
        return [e.en for e in self.g.find(text)]

    def test_only_terms_of_the_string(self):
        self.assertEqual(self.names("Bulbasaur sleeps."), ["Bulbasaur"])
        self.assertEqual(self.names("It sleeps in the sun."), [])

    def test_two_word_form_wins(self):
        self.assertEqual(self.names("It uses Leech Seed now."), ["Leech Seed"])
        self.assertEqual(self.names("This also raises the Sp. Atk stat."), ["Sp. Atk"])

    def test_word_forms_and_possessive(self):
        self.assertEqual(self.names("The user's head"), ["user"])
        self.assertEqual(self.names("It may make it flinch."), ["flinch"])
        self.assertEqual(self.names("It flinched."), ["flinch"])
        self.assertEqual(self.names("Heracross’s horn"), ["Heracross"])

    def test_case_matters(self):
        self.assertEqual(self.names("It can fly and bind stone."), [])

    def test_type_word_is_not_the_block(self):
        self.assertEqual(self.names("It hits a Fire-type target."), ["target"])

    def test_verb_moves_is_not_a_term(self):
        self.assertEqual(self.names("It moves freely in the water."), [])
        self.assertEqual(self.names("This move always goes first."), ["move"])

    def test_common_word_at_sentence_start_is_not_a_term(self):
        self.assertEqual(self.names("Fly high. Bind tight."), [])
        self.assertEqual(self.names("It learned Bind."), ["Bind"])

    def test_pokemon_at_sentence_start_is_a_term(self):
        self.assertEqual(self.names("Mew sleeps. Heracross wakes."), ["Mew", "Heracross"])

    def test_common_word_in_a_title_is_not_a_term(self):
        self.assertEqual(self.names("Fly Stone Mode"), [])
        self.assertEqual(self.names("Fly Stone Mode with Heracross"), ["Heracross"])
        self.assertEqual(self.names("It learned Bind and Fly today."), ["Bind", "Fly"])

    def test_part_of_a_longer_word_is_not_a_term(self):
        self.assertEqual(self.names("The Mewtwo and Mew-like Bulbasaurs"), [])

    def test_code_is_not_scanned(self):
        self.assertEqual(self.names("Use minecraft:target_block and targetValue"), [])


class PromptNote(unittest.TestCase):
    def test_only_used_terms(self):
        note = glossary().prompt_note(["The user hits Bulbasaur."])
        self.assertIn("Bulbasaur = Бульбазавр", note)
        self.assertIn("user = пользователь", note)
        self.assertNotIn("Heracross", note)
        self.assertEqual(note.count(" = "), 2)

    def test_no_terms_means_empty(self):
        self.assertEqual(glossary().prompt_note(["Plain text"]), "")

    def test_each_term_once_for_several_strings(self):
        self.assertEqual(glossary().prompt_note(["Mew sleeps.", "Mew wakes."]).count("Мью"), 1)


class Missing(unittest.TestCase):
    def setUp(self):
        self.g = glossary()

    def test_declined_name_counts(self):
        self.assertEqual(self.g.missing("Bulbasaur sleeps.", "Бульбазавра не видно."), [])
        self.assertEqual(self.g.missing("It hits Heracross.", "Удар по Геракроссу."), [])

    def test_lost_or_other_name_is_named(self):
        self.assertEqual(self.g.missing("Heracross sleeps.", "Херакрос спит."), ["Heracross"])
        self.assertEqual(self.g.missing("Bulbasaur sleeps.", "Покемон спит."), ["Bulbasaur"])

    def test_player_instead_of_user_is_rejected(self):
        self.assertEqual(self.g.missing("The user attacks.", "Игрок нападает."), ["user"])
        self.assertEqual(self.g.missing("The user attacks.", "Пользователь нападает."), [])

    def test_common_terms_by_root(self):
        self.assertEqual(self.g.missing("It may make the target flinch.", "Может заставить цель вздрогнуть."), [])
        self.assertEqual(self.g.missing("It may make the target flinch.", "Цель может морщиться."), ["flinch"])
        self.assertEqual(self.g.missing("This move hits.", "Этот приём бьёт."), [])
        self.assertEqual(self.g.missing("This move hits.", "Этот прием бьёт."), [])
        self.assertEqual(self.g.missing("It may cause paralysis.", "Может парализовать."), [])
        self.assertEqual(self.g.missing("Critical hits land.", "Критические удары проходят."), [])
        self.assertEqual(self.g.missing("It raises Sp. Atk.", "Повышает спец. атаку."), [])
        self.assertEqual(self.g.missing("It raises Sp. Atk.", "Повышает атаку."), ["Sp. Atk"])

    def test_every_word_of_a_name_is_needed(self):
        self.assertEqual(self.g.missing("It learned Headbutt.", "Выучил удар."), ["Headbutt"])
        self.assertEqual(self.g.missing("It learned Headbutt.", "Выучил Удар Головой."), [])

    def test_prompt_only_terms_do_not_reject(self):
        self.assertEqual(self.g.missing("The Pokémon lowers the stat.", "Оно снижает его."), [])
        self.assertEqual(self.g.missing("Throw a Poké Ball at a Stone.", "Бросьте шар в скалу."), [])

    def test_answer_equal_to_source_is_not_checked(self):
        self.assertEqual(self.g.missing("Bulbasaur sleeps.", "Bulbasaur sleeps."), [])

    def test_roots(self):
        self.assertEqual(roots_of("Бульбазавр"), ("бульбаза",))
        self.assertEqual(roots_of("Полёт"), ("поле",))
        self.assertEqual(roots_of("Мью"), ("мью",))

    def test_echo_of_the_prompt(self):
        self.assertTrue(self.g.echoed("Bulbasaur = Бульбазавр"))
        self.assertTrue(self.g.echoed("Glossary. Use exactly these translations"))
        self.assertFalse(self.g.echoed("Бульбазавр спит."))
        self.assertFalse(self.g.echoed("x = значение"))


class Model:
    name = "m"

    def __init__(self, reply=None):
        self.reply = reply or (lambda text: "Пользователь бьёт Бульбазавра.")
        self.systems, self.users = [], []

    def chat(self, messages, **kwargs):
        self.systems.append(messages[0]["content"])
        self.users.append(messages[-1]["content"])
        if "JSON array" in messages[0]["content"]:
            return json.dumps([self.reply(x) for x in json.loads(messages[-1]["content"])], ensure_ascii=False)
        return self.reply(messages[-1]["content"])


def translator(model, with_glossary=True):
    tr = Translator(model, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                    log=lambda s: None, target_lang="ru_ru")
    tr.max_attempts = 1
    if with_glossary:
        tr.set_glossary(glossary())
    return tr


class Translation(unittest.TestCase):
    SOURCE = "The user hits Bulbasaur."

    def test_terms_reach_the_prompt_and_answer_is_cached(self):
        model = Model()
        tr = translator(model)
        self.assertEqual(tr.translate(self.SOURCE), "Пользователь бьёт Бульбазавра.")
        self.assertIn("Bulbasaur = Бульбазавр", model.systems[0])
        self.assertIn("user = пользователь", model.systems[0])
        self.assertEqual(tr.cache.get(self.SOURCE, "ru_ru"), "Пользователь бьёт Бульбазавра.")

    def test_string_without_terms_keeps_the_prompt_byte_for_byte(self):
        plain, with_terms = Model(lambda text: "Простой текст"), Model(lambda text: "Простой текст")
        translator(plain, with_glossary=False).translate("Plain text")
        translator(with_terms).translate("Plain text")
        self.assertEqual(plain.systems, with_terms.systems)

    def test_lost_term_rolls_back_and_goes_to_second_pass(self):
        tr = translator(Model(lambda text: "Игрок бьёт Бульбазавра."))
        self.assertEqual(tr.translate(self.SOURCE), self.SOURCE)
        self.assertIsNone(tr.cache.get(self.SOURCE, "ru_ru"))
        self.assertEqual(tr.take_rejected(), [(self.SOURCE, "ru_ru")])

    def test_second_pass_with_the_strong_model_fixes_it(self):
        tr = translator(Model(lambda text: "Игрок бьёт Бульбазавра."))
        tr.translate(self.SOURCE)
        strong = Model()
        self.assertEqual(tr.translate(self.SOURCE, client=strong), "Пользователь бьёт Бульбазавра.")
        self.assertIn("Bulbasaur = Бульбазавр", strong.systems[0])

    def test_echo_is_rejected(self):
        tr = translator(Model(lambda text: "Bulbasaur = Бульбазавр\nuser = пользователь"))
        self.assertEqual(tr.translate(self.SOURCE), self.SOURCE)

    def test_batch_has_terms_of_all_its_strings_and_rejects_one(self):
        model = Model(lambda text: "Этот Мью спит." if "Mew" in text else "Простая строка")
        tr = translator(model)
        with contextlib.redirect_stdout(io.StringIO()):
            result = tr.translate_many(["Mew sleeps.", "Plain text", "Heracross sleeps."])
        self.assertEqual(result, ["Этот Мью спит.", "Простая строка", "Heracross sleeps."])
        self.assertIn("Mew = Мью", model.systems[0])
        self.assertIn("Heracross = Геракросс", model.systems[0])
        self.assertIsNone(tr.cache.get("Heracross sleeps.", "ru_ru"))

    def test_no_glossary_works_as_before(self):
        tr = translator(Model(lambda text: "Игрок бьёт Бульбазавра."), with_glossary=False)
        self.assertEqual(tr.translate(self.SOURCE), "Игрок бьёт Бульбазавра.")


class Pipeline(unittest.TestCase):
    def _run(self, reply):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(base, "mods"))
        with zipfile.ZipFile(os.path.join(base, "mods", "c.jar"), "w") as z:
            z.writestr("assets/cobblemon/lang/en_us.json", json.dumps({
                "cobblemon.species.heracross.name": "Heracross",
                "cobblemon.species.heracross.desc": "Heracross sleeps.",
            }))
            z.writestr("assets/cobblemon/lang/ru_ru.json", json.dumps({
                "cobblemon.species.heracross.name": "Геракросс",
            }, ensure_ascii=False))
        model, logs = Model(reply), []
        tr = Translator(model, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        tr.max_attempts = 1
        with contextlib.redirect_stdout(io.StringIO()):
            mirrorer.mirror_translate_dir(base, out, tr, log=logs.append, target_lang="ru_ru")
        name = [n for n in os.listdir(out) if n.endswith(".zip")]
        pack = {}
        if name:
            with zipfile.ZipFile(os.path.join(out, name[0])) as z:
                pack = json.loads(z.read("assets/cobblemon/lang/ru_ru.json"))
        return model, pack, logs

    def test_terms_come_from_the_pack_itself(self):
        model, pack, logs = self._run(lambda text: "Геракросс спит.")
        self.assertIn("Heracross = Геракросс", model.systems[0])
        self.assertEqual(pack, {"cobblemon.species.heracross.desc": "Геракросс спит."})
        self.assertTrue(any(line.startswith("[GLOSSARY]") for line in logs))

    def test_wrong_name_stays_out_of_the_pack(self):
        _, pack, _ = self._run(lambda text: "Херакрос спит.")
        self.assertEqual(pack, {})

    def test_setting_turns_it_off(self):
        with mock.patch.object(config, "GLOSSARY_ENABLED", False):
            model, pack, logs = self._run(lambda text: "Херакрос спит.")
        self.assertNotIn("Glossary", model.systems[0])
        self.assertEqual(pack, {"cobblemon.species.heracross.desc": "Херакрос спит."})
        self.assertFalse(any(line.startswith("[GLOSSARY]") for line in logs))


if __name__ == "__main__":
    unittest.main()
