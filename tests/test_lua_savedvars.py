"""The Lua string emitter and the SavedVariables parser (round trips and hostile input)."""
from __future__ import annotations

import random
import unittest

from mforge.errors import ValidationError
from mforge.lua import lua_str, render_data
from mforge.savedvars import MAX_DEPTH, MAX_SIZE, parse_sv

SAMPLE = '''\
MacroForgeExport = {
["char"] = "Testy",
["level"] = 30,
["ratio"] = 1.5e+3,
["flag"] = true,
["nothing"] = nil,
["pet"] = {
["family"] = "Imp",
["actions"] = {
"Firebolt", -- [1]
"Fire Shield", -- [2]
},
},
["names"] = { [1] = "a", [2] = "b" },
}
MacroForgeSeen = 0
'''


class LuaStrTests(unittest.TestCase):
    def roundtrip(self, text: str) -> str:
        return parse_sv(f"V = {lua_str(text)}")["V"]

    def test_basic_escaping(self):
        self.assertEqual(lua_str('a"b\\c\nd'), r'"a\"b\\c\nd"')

    def test_special_cases_round_trip(self):
        for text in ('"', "\\", "\n", "\r\n", "\t", "\x00", "\x01\x1f\x7f", "]]", "]=]", "--",
                     "[[", '"; os.execute("x"); --', "café 中文", "", "a" * 1000,
                     "\\n literal", '\\"'):
            self.assertEqual(self.roundtrip(text), text, repr(text))

    def test_random_strings_round_trip(self):
        rng = random.Random(1234)
        alphabet = ['"', "\\", "\n", "\r", "\t", "\x00", "]", "[", "=", "-", "|", " ", "a", "Z", "9",
                    "é", "中", "\x7f", "\x1b"]
        for _ in range(500):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
            self.assertEqual(self.roundtrip(text), text, repr(text))

    def test_output_never_contains_raw_newline_or_control_chars(self):
        out = lua_str("a\nb\rc\x00d\x1f")
        self.assertTrue(all(ord(c) >= 32 for c in out))


class RenderDataTests(unittest.TestCase):
    def test_data_file_parses_back_to_the_spec(self):
        spec = {"account": [{"name": "Tap", "icon": 136126, "body": '/say "hi"\n/cast Life Tap'}],
                "chars": {"Testy Mage": [{"name": "Fear", "icon": 5, "body": "/cast Fear\\x"}]},
                "remove": {"account": ["Old"], "chars": {"Testy Mage": ["T1", "T2"]}}}
        replies = [{"id": 7, "req": 3, "text": 'done "ok"'}]
        data = parse_sv(render_data(spec, 123, replies))["MacroForgeData"]
        self.assertEqual(data["rev"], 123)
        self.assertEqual(data["account"][0]["body"], '/say "hi"\n/cast Life Tap')
        self.assertEqual(data["chars"]["Testy Mage"][0]["body"], "/cast Fear\\x")
        self.assertEqual(data["remove"]["chars"]["Testy Mage"], ["T1", "T2"])
        self.assertEqual(data["replies"][0], {"id": 7, "req": 3, "text": 'done "ok"'})

    def test_hostile_text_cannot_break_out_of_the_literal(self):
        evil = '"}} os.execute("calc") --'
        spec = {"account": [{"name": "x", "icon": 1, "body": evil}], "chars": {},
                "remove": {"account": [], "chars": {}}}
        data = parse_sv(render_data(spec, 1, []))["MacroForgeData"]
        self.assertEqual(data["account"][0]["body"], evil)


class ParserTests(unittest.TestCase):
    def test_sample(self):
        data = parse_sv(SAMPLE)
        export = data["MacroForgeExport"]
        self.assertEqual(export["char"], "Testy")
        self.assertEqual(export["level"], 30)
        self.assertEqual(export["ratio"], 1500.0)
        self.assertIs(export["flag"], True)
        self.assertEqual(export["pet"]["actions"], ["Firebolt", "Fire Shield"])
        self.assertEqual(export["names"], {1: "a", 2: "b"})
        self.assertEqual(data["MacroForgeSeen"], 0)

    def test_comments_single_quotes_long_brackets_and_identifier_keys(self):
        text = ("-- header\n--[[ block\ncomment ]]\nA = { name = 'x\\'y', [[raw\\n]], "
                "[=[a]]b]=], count = 3; }\nB = \"\\65\\x41\\z\n   Z\"\n")
        data = parse_sv(text)
        self.assertEqual(data["A"]["name"], "x'y")
        self.assertEqual(data["A"][1], "raw\\n")
        self.assertEqual(data["A"][2], "a]]b")
        self.assertEqual(data["A"]["count"], 3)
        self.assertEqual(data["B"], "AAZ")

    def test_empty_table_is_empty_list(self):
        self.assertEqual(parse_sv("X = {}")["X"], [])

    def test_hex_and_negative_numbers(self):
        self.assertEqual(parse_sv("X = {0x10, -5, -2.5}")["X"], [16, -5, -2.5])

    def test_malformed_inputs_raise_validation_error(self):
        for bad in ("X = {", 'X = "abc', "X = {1, 2", "X = }", "X", "= 5", "X = [", "X = [[abc",
                    "X = {[1 = 2}", "X = 5 5 5 =", "X = \\", "X = {1 2}", "X = \"a\\xZZ\"",
                    'X = "a\nb"', "X = nope", "X = {[{}] = 1}", "X = {[nil] = 1}", "1 = 2"):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                parse_sv(bad)

    def test_depth_limit(self):
        deep = "X = " + "{" * (MAX_DEPTH + 5) + "}" * (MAX_DEPTH + 5)
        with self.assertRaisesRegex(ValidationError, "nested"):
            parse_sv(deep)
        ok = "X = " + "{" * (MAX_DEPTH - 2) + "}" * (MAX_DEPTH - 2)
        parse_sv(ok)

    def test_size_limit(self):
        with self.assertRaisesRegex(ValidationError, "too large"):
            parse_sv("X = " + '"' + "a" * MAX_SIZE + '"')

    def test_fuzz_only_validation_errors(self):
        rng = random.Random(20261008)
        seeds = [SAMPLE, 'A = { "x", { ["k"] = "v\\n" }, 5, true }\n',
                 "X = [[long]] Y = 'q' Z = {0x1F, 1e5}\n"]
        pieces = ['"', "'", "{", "}", "[", "]", "=", ",", ";", "\\", "--", "[[", "]]", "\n", "nil",
                  "true", "0x", "1e", ".", "-", " ", "\x00", "é"]
        for _ in range(2000):
            text = rng.choice(seeds)
            for _edit in range(rng.randint(1, 6)):
                pos = rng.randint(0, len(text))
                if rng.random() < 0.5:
                    text = text[:pos] + rng.choice(pieces) + text[pos:]
                else:
                    text = text[:pos] + text[pos + rng.randint(1, 4):]
            try:
                parse_sv(text)
            except ValidationError:
                pass  # any other exception type fails the test


if __name__ == "__main__":
    unittest.main()
