"""Validation at the trust boundaries: requests, macro text and reply text."""
from __future__ import annotations

import unittest

from mforge.errors import ValidationError
from mforge.validate import (check_blocked, clean_macro, command_of, norm, parse_request,
                             sanitize_reply)

CLASSES = frozenset({"warlock", "mage", "hunter"})
GOOD = {"id": 179143700001, "text": "heal me", "char": "Testy Mage", "class": "WARLOCK", "level": 30}


class NormTests(unittest.TestCase):
    def test_spaces_hyphens_and_case_are_ignored(self):
        self.assertEqual(norm("Testy Mage"), norm("testy-mage"))
        self.assertEqual(norm("Testy Mage"), "testymage")


class ParseRequestTests(unittest.TestCase):
    def test_valid_request(self):
        req = parse_request(GOOD, CLASSES)
        self.assertEqual((req.id, req.char, req.cls, req.level), (179143700001, "Testy Mage", "warlock", 30))

    def test_non_table_rejected(self):
        for raw in (None, "text", 5, [GOOD]):
            with self.assertRaises(ValidationError):
                parse_request(raw, CLASSES)

    def test_bad_ids_rejected(self):
        for bad in (None, "5", 1.5, True, 0, -3):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                parse_request({**GOOD, "id": bad}, CLASSES)

    def test_class_must_be_known_and_never_a_path(self):
        for bad in ("../../x", "..\\..\\evil", "warlock/../x", "", None, 5, "notaclass", "war lock"):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                parse_request({**GOOD, "class": bad}, CLASSES)

    def test_character_name_rules(self):
        for bad in ("", "a", "../x", "Name|cffff0000", "x" * 60, "Na\nme", None, 7):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                parse_request({**GOOD, "char": bad}, CLASSES)
        for ok in ("Testy Mage", "Testy-Mage", "D'arcy Vane", "Zoë Müller"):
            self.assertEqual(parse_request({**GOOD, "char": ok}, CLASSES).char, ok)

    def test_level_range(self):
        for bad in (0, 101, -1, "30", 30.5, True, None):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                parse_request({**GOOD, "level": bad}, CLASSES)

    def test_text_is_required_trimmed_and_cleaned(self):
        for bad in (None, "", "   ", 5):
            with self.assertRaises(ValidationError):
                parse_request({**GOOD, "text": bad}, CLASSES)
        req = parse_request({**GOOD, "text": "a" * 900 + "\x00\x07"}, CLASSES, max_text=500)
        self.assertEqual(len(req.text), 500)
        req = parse_request({**GOOD, "text": "hi\x00there"}, CLASSES)
        self.assertNotIn("\x00", req.text)


class CleanMacroTests(unittest.TestCase):
    def test_plain_macro_passes_and_is_normalized(self):
        self.assertEqual(clean_macro("Tap", "\n/cast Life Tap\r\n/say hi\n"),
                         ("Tap", "/cast Life Tap\n/say hi"))

    def test_wrong_types_rejected(self):
        for name, body in ((None, "x"), ("", "x"), (5, "x"), ("n", None), ("n", 5), ("n", ["x"])):
            with self.assertRaises(ValidationError, msg=repr((name, body))):
                clean_macro(name, body)

    def test_length_limits(self):
        with self.assertRaisesRegex(ValidationError, "255"):
            clean_macro("n", "x" * 256)
        clean_macro("n", "x" * 255)
        with self.assertRaisesRegex(ValidationError, "16"):
            clean_macro("n" * 17, "x")

    def test_body_limit_counts_bytes_not_characters(self):
        clean_macro("n", "é" * 127 + "x")  # 255 bytes
        with self.assertRaisesRegex(ValidationError, "255"):
            clean_macro("n", "é" * 128)  # 128 chars but 256 bytes

    def test_name_rejects_pipe_quote_and_control(self):
        for bad in ("a|cffff0000b", 'say "hi"', "a\nb", "a\x00b"):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                clean_macro(bad, "/cast X")

    def test_body_rejects_control_characters_and_escape_sequences(self):
        for bad in ("/say \x00", "/say \x1b[0m", "/say |cffff0000red|r", "/say |Hitem:1|h[x]|h",
                    "/say |TInterface\\Icons\\x:16|t", "/say a||b", "/say line|nbreak"):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                clean_macro("n", bad)

    def test_lone_pipe_followed_by_space_is_allowed(self):
        clean_macro("n", "/say a | b")


class BlockedCommandTests(unittest.TestCase):
    BLOCKED = {"script", "run", "dump", "console"}

    def test_direct_commands_blocked(self):
        for body in ("/script x", "/run x", "/dump x", "/console x"):
            with self.assertRaises(ValidationError, msg=body):
                check_blocked("n", body, self.BLOCKED)

    def test_evasion_attempts_blocked(self):
        for body in ("/RUN x", "  /script x", "\t/run x", "/run(print(1))", "/ script x",
                     "/cast X\n/Script y", "/dump{1}"):
            with self.assertRaises(ValidationError, msg=repr(body)):
                check_blocked("n", body, self.BLOCKED)

    def test_ordinary_commands_pass(self):
        check_blocked("n", "#showtooltip\n/cast [harm] Fear\n/stopmacro [combat]\n/target player",
                      self.BLOCKED)

    def test_command_of(self):
        self.assertEqual(command_of("  /Cast [x] y"), "cast")
        self.assertEqual(command_of("/instance_chat hi"), "instance_chat")
        self.assertIsNone(command_of("#showtooltip"))


class SanitizeReplyTests(unittest.TestCase):
    def test_chat_escapes_are_removed(self):
        out = sanitize_reply("done |cffff0000RED|r |Hitem:19019|h[Thunderfury]|h |TInterface\\x|t")
        self.assertNotIn("|", out)

    def test_control_and_non_ascii_are_neutralized(self):
        out = sanitize_reply("café\x00\x07 ok\n\nnext")
        self.assertTrue(out.isascii())
        self.assertNotIn("\x00", out)
        self.assertNotIn("\n", out)

    def test_length_cap(self):
        self.assertEqual(len(sanitize_reply("x" * 5000)), 600)
        self.assertEqual(len(sanitize_reply("x" * 5000, limit=50)), 50)


if __name__ == "__main__":
    unittest.main()
