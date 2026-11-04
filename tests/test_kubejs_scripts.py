"""KubeJS: переводятся только Text.of и tell, скрипт остаётся корректным при любом ответе модели."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import os
import unittest

from src.processors.kubejs_js import translate_kubejs_script_text


class Fixed:
    def __init__(self, answer=None):
        self.answer = answer
        self.sent = []

    def translate(self, text, **kwargs):
        self.sent.append(text)
        return self.answer if self.answer is not None else "ПЕР " + text


class Selection(unittest.TestCase):
    def _sent(self, script):
        tr = Fixed()
        translate_kubejs_script_text(script, tr)
        return tr.sent

    def test_text_of_and_tell_are_translated(self):
        self.assertEqual(self._sent('Text.of("One text")'), ["One text"])
        self.assertEqual(self._sent('player.tell("Two text")'), ["Two text"])
        self.assertEqual(self._sent('server.tell(`Three text`)'), ["Three text"])

    def test_console_log_and_send_message_are_left_alone(self):
        for script in ('console.log("Debug text")', 'sendMessage("Message text")', 'hotell("No text")'):
            self.assertEqual(self._sent(script), [], script)

    def test_comments_and_strings_are_not_calls(self):
        for script in ('// tell("in comment")', '/* Text.of("in block") */', 'var s = "tell(\'inner\')"'):
            self.assertEqual(self._sent(script), [], script)

    def test_templates_and_unknown_escapes_are_skipped(self):
        for script in ('tell(`Has ${x} value`)', 'Text.of("\\u00a7aColored")'):
            self.assertEqual(self._sent(script), [], script)

    def test_only_the_first_literal_changes(self):
        out = translate_kubejs_script_text('Text.of("One").append("Two").red()', Fixed())
        self.assertEqual(out, 'Text.of("ПЕР One").append("Two").red()')


class Encoding(unittest.TestCase):
    def _out(self, script, answer):
        return translate_kubejs_script_text(script, Fixed(answer))

    def test_escaped_quote_in_source(self):
        tr = Fixed()
        out = translate_kubejs_script_text("tell('It\\'s ok')", tr)
        self.assertEqual(tr.sent, ["It's ok"])
        self.assertEqual(out, "tell('ПЕР It\\'s ok')")

    def test_quote_in_answer_is_escaped(self):
        self.assertEqual(self._out('tell("x y")', 'Он "да"'), 'tell("Он \\"да\\"")')

    def test_real_newline_becomes_escape(self):
        self.assertEqual(self._out('tell("x y")', "Две\nстроки"), 'tell("Две\\nстроки")')

    def test_backslash_is_doubled(self):
        self.assertEqual(self._out('tell("x y")', "конец\\"), 'tell("конец\\\\")')

    def test_kept_escape_survives(self):
        self.assertEqual(self._out('tell("a\\nb")', "а\\nб"), 'tell("а\\nб")')

    @unittest.skipUnless(shutil.which("node"), "нужен node")
    def test_dangerous_answers_keep_the_script_valid(self):
        answers = ['Он сказал "да" и \'нет\'', "Слэш \\ внутри \\", "Две\nстроки", "Закрыть ); скобку",
                   "Шаблон ${x} `тик`", "// комментарий", "конец\\", "*/ /*"]
        scripts = ['Text.of("One text").red()', "player.tell('Two\\'s text')", "tell(`Three text`, 1)"]
        path = os.path.join(tempfile.mkdtemp(), "x.js")
        for answer in answers:
            for script in scripts:
                out = translate_kubejs_script_text(f"function f(player) {{ {script} }}", Fixed(answer))
                with open(path, "w", encoding="utf-8") as f:
                    f.write(out)
                result = subprocess.run(["node", "--check", path], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f"{answer!r} {script!r} -> {out}\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()
