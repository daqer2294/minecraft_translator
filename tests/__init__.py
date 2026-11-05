# Модели-заглушки во многих тестах отвечают «ПЕР » + английский источник. Настоящая проверка такой ответ
# отбрасывает как недоперевод, а те тесты проверяют другое. Сама проверка покрыта в test_leftover_english.
from src import translators as _translators

_translators.leftover_english_words = lambda source, answer, target_lang: []
