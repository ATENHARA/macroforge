"""Regression tests for the script checker bypasses found in the adversarial review.

Every script in ``REFUSED`` got through an earlier version of the checker. Each one must now be
refused, and ``ACCEPTED`` lists the ordinary scripts the stricter rules must keep allowing.
"""
from __future__ import annotations

import unittest

from mforge.errors import ValidationError
from mforge.luacheck import ApiKnowledge, check_script, check_script_body, script_lines
from mforge.luaparse import parse

# (code, text the refusal message must contain)
REFUSED = {
    "game table that is not a C_ namespace": ('SomeTable.DoIt("x")', "only reading functions"),
    "banned name in parentheses": ('(SendChatMessage)("x","SAY")', "chat"),
    "denied field of a call result": ("MF_x=GetMouseFocus().SendChatMessage", "chat"),
    # the game's own command dispatchers and edit box
    "slash command table": ('SlashCmdList.SCRIPT("SendChatMessage(\'hi\',\'GUILD\')")', "SlashCmdList"),
    "secure command table": ('SecureCmdList.CAST("Fear")', "SecureCmdList"),
    "run macro text": ('RunMacroText("/cast Fear")', "RunMacroText"),
    "chat box": ('ChatFrame_OpenChat("/w x hi")', "ChatFrame_OpenChat"),
    "edit box text": ('local e=DEFAULT_CHAT_FRAME.editBox e:SetText("/w Bob hi")', "not allowed"),
    "edit box enter": ('local e=DEFAULT_CHAT_FRAME.editBox e:GetScript("OnEnterPressed")(e)',
                       "name can be called"),
    "edit box handler in a local": (
        'local e=DEFAULT_CHAT_FRAME.editBox local h=e:GetScript("OnEnterPressed") h(e)',
        "cannot be called"),
    "click a pop-up in a loop": ('for i=1,1000 do GetClickFrame("StaticPopup1Button1"):Click() end',
                                 "not allowed"),
    # methods and fields reached through parentheses, calls and aliases
    "paren base set script": ('(DEFAULT_CHAT_FRAME).SetScript(DEFAULT_CHAT_FRAME,"OnUpdate",'
                              'function() MF_n=(MF_n or 0)+1 end)', "SetScript"),
    "paren base in a local": ('local f=GetMouseFocus();(f).SetScript(f,"OnUpdate",print)', "SetScript"),
    "call result dot call": ('GetMouseFocus().RegisterEvent(GetMouseFocus(),"X")', "name can be called"),
    "call result field local": ('local f=GetMouseFocus() f.RegisterEvent(f,"X")', "RegisterEvent"),
    "paren string rep": ('MF_x=("x").rep("x",2^30)', "name can be called"),
    "paren string dump": ("local d=(string).dump print(d)", "dump"),
    "frame method through its constant": ("DEFAULT_CHAT_FRAME.Hide(DEFAULT_CHAT_FRAME)", "data"),
    "frame method in a loop": ("for i=1,1000 do DEFAULT_CHAT_FRAME.Hide(DEFAULT_CHAT_FRAME) end", "data"),
    "dot call through a game frame": ('ChatFrame1EditBox.SetText(ChatFrame1EditBox,"/w x hi")',
                                      "not one of your variables"),
    "alias of a frame method": ("local h=DEFAULT_CHAT_FRAME.Hide h(DEFAULT_CHAT_FRAME)", "cannot be called"),
    "alias given a new value": ("local h=print for i=1,2 do h(1) h=DEFAULT_CHAT_FRAME.Hide end",
                                "another value"),
    "call through a local table field": ("local t=string MF_x=t.format('%d',1)", "field of a variable"),
    "pcall a game function": ("pcall(DEFAULT_CHAT_FRAME.Hide,DEFAULT_CHAT_FRAME)", "pcall"),
    "xpcall": ("xpcall(print,print)", "xpcall"),
    "getglobal": ('getglobal("SendChatMessage")("hi","SAY")', "name can be called"),
    "game function as a gsub callback": ('local s=gsub("a","a",DEFAULT_CHAT_FRAME.Hide)', "pass a function"),
    "game function as a sort callback": ("table.sort({1,2},DEFAULT_CHAT_FRAME.Hide)", "pass a function"),
    "error handler": ("seterrorhandler(print)", "seterrorhandler"),
    # functions that can run forever or call themselves
    "function stored in a table": ("local t={} t.f=function() end", "declared with local"),
    "function in a constructor": ("local t={f=function() end}", "declared with local"),
    "global function statement": ("function MF_f() end", "declare a function with local"),
    "method statement": ("local t={} function t:f() self:f() end t:f()", "declare a function with local"),
    "tail call through self": ("local t={GetX=function(self) return self:GetX() end} t:GetX()",
                               "declared with local"),
    "y combinator": ("local function f(g) for i=1,1000 do g(g) end end f(f)", "cannot be called"),
    "own variable called": ("MF_f=print MF_f(1)", "cannot be called"),
    "custom iterator": ("local function pairs() return function() return 1 end end for x in pairs() do end",
                        "declared with local"),
    "custom iterator table": ("local t={gmatch=function() return function() return 1 end end} "
                              "for x in t.gmatch() do end", "declared with local"),
    "shadowed pairs": ("local pairs=print for x in pairs(1) do end", "for ... in needs"),
    # loops that never end or do too much
    "step zero": ("for i=2,1,0 do end", "step"),
    "step negative zero": ("for i=2,1,-0 do end", "step"),
    "step tiny": ("for i=1,2,1e-300 do end", "step"),
    "step fraction": ("for i=1,2,0.5 do end", "step"),
    "step in parentheses": ("for i=2,1,(0) do end", "plain numbers"),
    "step from a list": ("for i=1,10,#'ab' do end", "plain numbers"),
    "huge bound": ("for i=1,1e999 do end", "plain numbers"),
    "nan bound": ("for i=0/0,1 do end", "plain numbers"),
    "loops multiplied through a function": (
        "local function f() for i=1,1000 do for j=1,1000 do end end end "
        "for i=1,1000 do for j=1,1000 do f() end end", "too many loop steps"),
    "nested thousand loops": ("for i=1,1000 do for j=1,1000 do end end", "too many loop steps"),
    "function called many times": (
        "local function f() for i=1,1000 do end end " + "f() " * 25, "too many loop steps"),
    "loop in a callback": ("table.sort({2,1},function(a,b) for i=1,1000 do end return a<b end)",
                           "too many loop steps"),
    # strings that double
    "doubling in straight code": ('local a="x" a=a..a', "joined with itself"),
    "gsub doubling": ('local a="x" for i=1,60 do a=a:gsub(".","%0%0") end', "gsub"),
    "gsub doubling global": ('local a="x" for i=1,60 do a=gsub(a,".","%0%0") end', "gsub"),
    "doubling through an alias": ('local a,b="x","" for i=1,100 do b=a a=a..b end', "does not change"),
    "format doubling": ('local a="x" for i=1,100 do a=format("%s%s",a,a) end', "more than once"),
    "concat doubling": ('local a="x" for i=1,100 do a=table.concat({a,a}) end', "more than once"),
    "doubling in a callback": ('local a="x" for i=1,9 do table.sort({2,1},function(p,q) a=a..a return p<q end) end',
                               "joined with itself"),
}

ACCEPTED = {
    "alias of a library function": 'local f=format print(f("%d",1))',
    "alias of a reading function": "local g=GetTime print(g())",
    "gmatch on a string": 'for w in ("a b"):gmatch("%S+") do print(w) end',
    "string.gmatch": 'for w in string.gmatch("a b","%S+") do print(w) end',
    "sort with your own comparator": "local t={3,1,2} table.sort(t,function(a,b) return a>b end) print(t[1])",
    "local function": "local function s(x) return x*2 end print(s(4))",
    "local f = function": "local s=function(x) return x*2 end print(s(4))",
    "string methods": 'local s="x" print(s:len(),s:upper(),("%d"):format(3))',
    "reading method on a game object": "print(GetMouseFocus():GetName())",
    "print to a frame": 'DEFAULT_CHAT_FRAME:AddMessage("hi")',
    "extend a string in a loop": 'local s="" for i=1,5 do s=s..i end print(s)',
    "pairs": "local t={a=1} for k,v in pairs(t) do print(k,v) end",
    "ipairs": "local t={1,2} for i,v in ipairs(t) do print(i,v) end",
    "break in a loop": "for i=1,5 do if i==3 then break end end",
    "step": "for i=10,1,-2 do print(i) end",
    "length bound": 'local t={1,2,3} for i=1,#t do print(t[i]) end',
    "one action": 'C_PetInfo.PetRename("x")',
    "two small loops": "local n=0 for i=1,18 do for j=1,18 do n=n+1 end end print(n)",
    "function called a few times": "local function f() for i=1,100 do end end f() f() f()",
    "parenthesised function name": '(print)("x")',
    "work of a function is its own": ("for i=1,1000 do end local function f() for i=1,10 do end end "
                                      + "f() " * 25),
    "data table read": "print(RAID_CLASS_COLORS.WARLOCK.colorStr)",
    "game table read call": "print(C_Map.GetBestMapForUnit('player'))",
}


class BypassTests(unittest.TestCase):
    def test_every_bypass_found_in_the_review_is_refused(self):
        for name, (code, text) in REFUSED.items():
            with self.subTest(name):
                with self.assertRaises(ValidationError) as caught:
                    check_script(code)
                self.assertIn(text, str(caught.exception), code)

    def test_ordinary_scripts_are_still_accepted(self):
        for name, code in ACCEPTED.items():
            with self.subTest(name):
                check_script(code)

    def test_the_dispatchers_are_refused_even_when_the_probe_lists_them(self):
        known = ApiKnowledge(names=frozenset({"SlashCmdList", "RunMacroText", "print"}))
        for code in ('SlashCmdList.SCRIPT("x")', 'RunMacroText("/cast Fear")'):
            with self.assertRaises(ValidationError):
                check_script(code, known=known)

    def test_a_dispatcher_cannot_hide_behind_a_local_or_a_string_key(self):
        for code in ('local s=SlashCmdList', 'MF_x=SlashCmdList["SCRIPT"]',
                     'local k="SCRIPT" SlashCmdList[k]("x")'):
            with self.assertRaises(ValidationError):
                check_script(code)

    def test_the_limit_of_one_action_survives_the_new_rules(self):
        with self.assertRaises(ValidationError):
            check_script('C_PetInfo.PetRename("a") C_PetInfo.PetRename("b")')
        with self.assertRaises(ValidationError):
            check_script('for i=1,2 do C_PetInfo.PetRename("a") end')


class ParserParityTests(unittest.TestCase):
    """What the game's Lua 5.1 refuses, the parser refuses too."""

    def test_break_needs_a_loop_in_the_same_function(self):
        for code in ("break", "if x then break end", "for i=1,2 do local f=function() break end end",
                     "local function f() break end"):
            with self.subTest(code=code), self.assertRaises(ValidationError):
                parse(code)
        for code in ("for i=1,2 do break end", "for i=1,2 do if i then break end end",
                     "for i=1,2 do local f=function() for j=1,2 do break end end end"):
            with self.subTest(code=code):
                parse(code)

    def test_a_long_string_may_not_contain_a_nested_long_bracket(self):
        for code in ("local s=[[a [[ b]]", "--[[ a [[ b ]]"):
            with self.subTest(code=code), self.assertRaises(ValidationError):
                parse(code)
        parse("local s=[=[a [[ b]=]")  # a different level is fine
        parse("local s=[[a ] ]]")


class MalformedInputTests(unittest.TestCase):
    """Whatever it is fed, the checker answers with a refusal or an acceptance, never a crash."""

    def test_odd_and_huge_input_never_crashes(self):
        cases = ["print(" + "(" * 5000 + "1" + ")" * 5000 + ")", "local t=" + "{" * 3000 + "}" * 3000,
                 "print(1)\x00print(2)", "print(" + "9" * 5000 + ")", "print(1e99999999999)",
                 "print(0x" + "f" * 500 + ")", "print('‮퟿\U0001F600')", "print('\ud800')",
                 "", "   ", "--[[ x ]]", "print('x", "local s=[[" + "x" * 100000 + "]]", ";" * 10000]
        for code in cases:
            with self.subTest(code=code[:30]):
                try:
                    check_script(code)
                except ValidationError:
                    pass

    def test_a_hex_number_too_big_for_a_double_is_infinite_so_not_a_loop_bound(self):
        with self.assertRaises(ValidationError):
            check_script("for i=1,0x" + "f" * 400 + " do end")


class MacroLineTests(unittest.TestCase):
    """The checker reads script lines the way the addon does (``^%s*/run%s+(%S.*)$``)."""

    def test_a_blank_or_whitespace_only_line_is_not_a_run_line(self):
        self.assertEqual(script_lines("/run print(1)\n/run print(2)"), ["print(1)", "print(2)"])
        for body in ("/run print(1)\n   ", "/run print(1)\n\t", "  \n/run print(1)"):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                script_lines(body)

    def test_unicode_spaces_do_not_count_as_whitespace(self):
        for body in ("/run print(1)", " /run print(1)", "/run  print(1)"):
            with self.subTest(body=body):
                if body.startswith("/run "):  # an NBSP then code: Lua sees a stray byte at compile time
                    self.assertEqual(script_lines(body), [" print(1)"])
                else:
                    with self.assertRaises(ValidationError):
                        script_lines(body)

    def test_a_body_with_surrounding_ascii_spaces_is_accepted(self):
        self.assertEqual(script_lines("  /run   print(1)"), ["print(1)"])
        check_script_body("/run print(1)")


if __name__ == "__main__":
    unittest.main()
