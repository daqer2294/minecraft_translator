"""Чистка кэша: удаляет только брак и всегда оставляет бэкап."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from tools.clean_mixed_cache import BACKUP_SUFFIX, clean_cache

DATA = {
    "ru_ru": {
        "Minecraft": "Мinecraft",
        "Show culling boxes": "Показать кulling кубы",
        "Iron Ingot": "Железный слиток",
        "Pokémon team": "Команда Pokémon",
        "Share %s": "Поделиться %s",
        "Sodium": "Sodium",
    },
    "de_de": {"Iron Ingot": "Eisenbarren"},
}


class CleanCache(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "cache.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(DATA, f, ensure_ascii=False)
        with open(self.path, "rb") as f:
            self.original = f.read()

    def _read(self, path=None):
        with open(path or self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_dry_run_changes_nothing(self):
        self.assertEqual(clean_cache(self.path), {"before": 7, "removed": 2, "after": 5})
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), self.original)
        self.assertFalse(os.path.exists(self.path + BACKUP_SUFFIX))

    def test_apply_removes_only_rejected_entries(self):
        self.assertEqual(clean_cache(self.path, apply=True), {"before": 7, "removed": 2, "after": 5})
        cleaned = self._read()
        self.assertNotIn("Minecraft", cleaned["ru_ru"])
        self.assertNotIn("Show culling boxes", cleaned["ru_ru"])
        for key in ("Iron Ingot", "Pokémon team", "Share %s", "Sodium"):
            self.assertEqual(cleaned["ru_ru"][key], DATA["ru_ru"][key])
        self.assertEqual(cleaned["de_de"], DATA["de_de"])

    def test_backup_is_byte_identical_to_the_original(self):
        clean_cache(self.path, apply=True)
        with open(self.path + BACKUP_SUFFIX, "rb") as f:
            self.assertEqual(f.read(), self.original)

    def test_existing_backup_is_not_overwritten(self):
        with open(self.path + BACKUP_SUFFIX, "w") as f:
            f.write("старый")
        with self.assertRaises(FileExistsError):
            clean_cache(self.path, apply=True)
        with open(self.path + BACKUP_SUFFIX) as f:
            self.assertEqual(f.read(), "старый")
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), self.original)

    def test_nothing_to_remove_makes_no_backup(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"ru_ru": {"Iron": "Железо"}}, f, ensure_ascii=False)
        self.assertEqual(clean_cache(self.path, apply=True)["removed"], 0)
        self.assertFalse(os.path.exists(self.path + BACKUP_SUFFIX))


if __name__ == "__main__":
    unittest.main()
