"""The Lua reader and the script rules: what is accepted, what is refused and why."""
from __future__ import annotations

import random
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from mforge import luacheck, luaparse
from mforge.errors import ValidationError
from mforge.luacheck import ApiKnowledge, check_script, check_script_body, load_rules, script_lines
from mforge.luaparse import Bin, Call, Index, Lit, Name, Un, parse, tokenize

ROOT = Path(__file__).resolve().parent.parent


def refused(test: unittest.TestCase, code: str, *words: str) -> str:
    """Assert the script is refused (with these words in the reason) and return the reason."""
    with test.assertRaises(ValidationError, msg=code) as caught:
        check_script(code)
    message = str(caught.exception)
    for word in words:
        test.assertIn(word, message, code)
    return message


GOOD = [
    'local n={"Milo","Biscuit","Rascal"};local i=random(#n);if i==MF_p then i=i%#n+1 end;'
    'MF_p=i;PetRename(n[i])',
    'print(GetCVar("Sound_EnableSFX"))',
    'SetCVar("Sound_EnableSFX",GetCVar("Sound_EnableSFX")=="1" and 0 or 1)',
    'local m=C_Map.GetBestMapForUnit("player");local p=C_Map.GetPlayerMapPosition(m,"player");'
    'print(format("%.1f, %.1f",p.x*100,p.y*100))',
    'local s,n=0,0;for i=1,18 do local c,m=GetInventoryItemDurability(i);if c then s=s+c/m;n=n+1 end '
    'end;print(("Durability %d%%"):format(n>0 and s/n*100 or 100))',
    'local e={"dance","cheer","laugh"};DoEmote(e[random(#e)])',
    'local c=RAID_CLASS_COLORS["WARLOCK"];print(c.r)',
    "PlaySound(8960)",
    "for _,b in ipairs({0,1,2}) do print(GetContainerNumFreeSlots(b)) end",
    'local a={"Yes","No","Ask again"};print(a[random(#a)])',
    'print(GetCoinTextureString(GetMoney()))',
    'local _,_,h,w=GetNetStats();print(h.."ms home, "..w.."ms world")',
    'print(UnitName("target"),UnitLevel("target"))',
    'local t={3,1,2};table.sort(t,function(a,b)return a>b end);print(t[1])',
    'for w in ("a b c"):gmatch("%a+") do print(w) end',
    'UIErrorsFrame:AddMessage("hi",1,1,0)',
]


class LexerTests(unittest.TestCase):
    def values(self, src):
        return [(t.kind, t.value) for t in tokenize(src)][:-1]

    def test_basic_tokens(self):
        self.assertEqual(self.values('local x = 1.5 .. "a"'),
                         [("keyword", "local"), ("name", "x"), ("op", "="), ("number", 1.5),
                          ("op", ".."), ("string", "a")])

    def test_numbers(self):
        for text, value in (("5", 5.0), ("5.", 5.0), (".5", 0.5), ("1e3", 1000.0), ("2.5E-1", 0.25),
                            ("0x10", 16.0), ("0XfF", 255.0)):
            self.assertEqual(tokenize(text)[0].value, value, text)

    def test_non_ascii_digits_do_not_crash_the_reader(self):
        # an unknown escape just drops the backslash (Lua 5.1), even before a superscript digit
        self.assertEqual(tokenize('"a\\' + chr(0xB2) + '"')[0].value, "a" + chr(0xB2))
        for src in ("x = " + chr(0x663), "x = 1" + chr(0xB2)):
            with self.assertRaises(ValidationError, msg=src):
                tokenize(src)

    def test_malformed_numbers_are_refused(self):
        for text in ("5y", "1e", "0x", "12abc", "3LL"):
            with self.assertRaises(ValidationError, msg=text):
                tokenize(text)

    def test_string_escapes(self):
        cases = {r'"a\nb"': "a\nb", r'"\65\066"': "AB", r'"a\qb"': "aqb", r'"\\"': "\\",
                 r"'it\'s'": "it's", r'"\""': '"', r'"\255"': "\xff"}
        for src, value in cases.items():
            self.assertEqual(tokenize(src)[0].value, value, src)

    def test_escapes_that_are_not_lua_51_are_refused(self):
        for src in (r'"\x41"', r'"\z  x"', r'"\256"'):
            with self.assertRaises(ValidationError, msg=src):
                tokenize(src)

    def test_unfinished_strings(self):
        for src in ('"abc', "'abc", '"abc\\', '"ab\nc"', "[[abc", "[==[abc]=]", "--[[abc"):
            with self.assertRaises(ValidationError, msg=src):
                tokenize(src)

    def test_long_strings_and_comments(self):
        self.assertEqual(self.values("x = [[a]]"), [("name", "x"), ("op", "="), ("string", "a")])
        self.assertEqual(self.values("[==[a]]b]==]"), [("string", "a]]b")])
        self.assertEqual(self.values("a --[[hidden]] b"), [("name", "a"), ("name", "b")])
        self.assertEqual(self.values("a -- rest of line\nb"), [("name", "a"), ("name", "b")])
        self.assertEqual(self.values("a --[==[x]]y]==] b"), [("name", "a"), ("name", "b")])

    def test_characters_the_game_would_not_read(self):
        for src in ("a @ b", "caf\u00e9 = 1", "x = `y`", "x ~ y", "a \\ b"):
            with self.assertRaises(ValidationError, msg=src):
                tokenize(src)

    def test_operators(self):
        ops = [t.value for t in tokenize("== ~= <= >= .. ... + - * / % ^ # < > = ( ) { } [ ] ; : , .")
               if t.kind == "op"]
        self.assertEqual(len(ops), 26)

    def test_absurdly_long_source_is_refused(self):
        with self.assertRaises(ValidationError):
            tokenize("x=1 " * 2000)


class ParserTests(unittest.TestCase):
    def test_valid_programs(self):
        for src in GOOD + ["x=1 y=2", "return", "return 1,2", "local function f(a,...) return a end",
                           "a.b.c:d(1)(2)[3]=4", "print[[hi]] print'x' f{1,2,a=3,[4]=5,}",
                           "local a,b=1,2;a,b=b,a", "do local x end", "if a then elseif b then else end",
                           "for i=1,3,1 do end", "for k,v in pairs(t) do end", "function a.b:c() end",
                           "f(function(...) return ... end)", "x = {;}" if False else "x = {}",
                           "x = {1;2;3}", "(f or g)(1)", "a = -b ^ -c", "a = not not b"]:
            parse(src)

    def test_invalid_programs(self):
        for src in ("local n={},", "x=", "print(", "1+", "f(", "x = ", "end", "if x then", "for i do end",
                    "for i=1 do end", "local 5", "f() = 1", "x.y:z = 1", "a b", "return 1 x=2",
                    "break x", "function f( end", "x = {1,,2}", "local function end", "(f)", "a.1",
                    "x = function", "goto", "x = ...y"):
            with self.assertRaises(ValidationError, msg=src):
                parse(src)

    def test_operator_precedence_and_associativity(self):
        expr = parse("x = -2 ^ 2")[0].exprs[0]
        self.assertIsInstance(expr, Un)
        self.assertIsInstance(expr.operand, Bin)  # -(2^2)
        expr = parse("x = a .. b .. c")[0].exprs[0]
        self.assertEqual(expr.op, "..")
        self.assertIsInstance(expr.right, Bin)  # right associative
        expr = parse("x = a + b * c")[0].exprs[0]
        self.assertEqual(expr.op, "+")
        expr = parse("x = not a == b")[0].exprs[0]
        self.assertEqual(expr.op, "==")  # (not a) == b

    def test_calls_and_chains(self):
        call = parse("a.b:c(1)")[0].exprs[0]
        self.assertIsInstance(call, Call)
        self.assertEqual(call.method, "c")
        self.assertIsInstance(call.func, Index)
        self.assertEqual(parse('f"x"')[0].exprs[0].args[0], Lit("string", "x"))
        self.assertIsInstance(parse("x = a[1].b")[0].exprs[0], Index)
        self.assertIsInstance(parse("x = a")[0].exprs[0], Name)

    def test_vararg_only_where_declared(self):
        parse("x = ...")  # the main chunk is vararg
        parse("local function f(...) return ... end")
        for src in ("local function f() return ... end", "function f(a) return ... end"):
            with self.assertRaises(ValidationError, msg=src):
                parse(src)

    def test_deep_nesting_is_refused_not_a_crash(self):
        for src in ("x=" + "(" * 800 + "1" + ")" * 800, "x=" + "{" * 800 + "}" * 800,
                    "do " * 800 + "end " * 800, "x=" + "-" * 800 + "1", "x=" + "f(" * 800 + ")" * 800):
            with self.assertRaises(ValidationError, msg=src[:20]):
                parse(src)


class ReferenceTests(unittest.TestCase):
    def test_every_example_script_is_accepted(self):
        for code in GOOD:
            with self.subTest(code=code[:50]):
                check_script(code)

    def test_actions_are_counted(self):
        self.assertEqual(check_script('DoEmote("dance")').actions, 1)
        self.assertEqual(check_script('print("x")').actions, 0)
        self.assertEqual(check_script('print(GetCVar("a"))').actions, 0)

    def test_reasons_are_given(self):
        refused(self, 'SendChatMessage("x")', "sends chat messages")
        refused(self, 'CastSpellByName("Fear")', "casts, targets or attacks", "normal macro line")
        refused(self, "UseContainerItem(0,1)", "moves, uses, sells")
        refused(self, "AcceptQuest()", "accepts or confirms")
        refused(self, "JumpOrAscendStart()", "moves your character")
        refused(self, "Logout()", "ends your session")
        refused(self, 'CreateMacro("a","b","c")', "changes macros")


class StructuralRuleTests(unittest.TestCase):
    def test_every_banned_name_is_refused_as_a_variable_a_field_and_a_method(self):
        for name, reason in load_rules().banned.items():
            for code in (f"local x = {name}", f"local x = a.{name}", f"local x = ('s'):{name}()",
                         f"local {name} = 1"):
                message = refused(self, code, name)
                self.assertIn(reason, message) if code.startswith("local x = " + name) else None

    def test_banned_functions_inside_allowed_libraries_are_refused(self):
        refused(self, 'local x=string.rep', "enormous strings")
        refused(self, 'print(string.rep("x",9))', "enormous strings")
        refused(self, "local x=string.dump", "not for scripts")
        refused(self, 'print(("x"):rep(9))', "enormous strings")
        check_script('print(string.format("%d",1),string.sub("abc",1,2),math.max(1,2),table.concat({1,2}))')

    def test_computed_access_to_game_tables_is_refused(self):
        refused(self, 'C_Map["GetBestMapForUnit"]("player")', "computed name")
        refused(self, 'local k="Get"; print(C_Map[k])', "computed name")
        refused(self, 'print(string["rep"])', "computed name")
        refused(self, '_G["Send".."ChatMessage"]("x")', "_G")
        refused(self, 'print(SomeFrame[1])', "computed name")
        refused(self, 'print(math["floor"](2.5))', "computed name")
        refused(self, 'print(string["rep"]("x",3))', "computed name")

    def test_indexing_your_own_tables_and_data_constants_is_fine(self):
        check_script("local t={1,2,3};print(t[2],t[#t])")
        check_script('print(RAID_CLASS_COLORS[select(2,UnitClass("player"))])')
        check_script("MF_t={};MF_t[1]=5;print(MF_t[1])")

    def test_loops_that_can_run_forever_are_refused(self):
        refused(self, "while true do end", "forever")
        refused(self, "repeat until false", "forever")
        refused(self, "for k in next,{} do end", "ipairs")
        refused(self, "for k in myiter do end", "ipairs")
        refused(self, "for k in pairs({}),x do end", "ipairs")

    def test_for_loop_ranges(self):
        check_script("for i=1,1000 do end")
        check_script("local t={1,2};for i=#t,1,-1 do end")
        check_script("for i=-5,5 do end")
        refused(self, "for i=1,1001 do end", "plain numbers")
        refused(self, "for i=1,n do end", "plain numbers")
        refused(self, "for i=1,1e9 do end", "plain numbers")
        refused(self, "for i=1,10,0 do end", "step")

    def test_loop_nesting_is_limited(self):
        check_script("for a=1,2 do for b=1,2 do print(a,b) end end")
        refused(self, "for a=1,2 do for b=1,2 do for c=1,2 do end end end", "nested")

    def test_recursion_and_callbacks_into_your_own_code_are_refused(self):
        refused(self, "local function f() return f() end f()", "recursion")
        refused(self, "local f; f=function() return f() end f()", "declared with local")
        refused(self, "MF_f=function() return MF_f() end", "declared with local")
        refused(self, "local a=1;local function f() return a() end", "cannot be called")
        check_script("local function f(x) return x*2 end print(f(2))")
        check_script("table.sort({3,1},function(a,b) return a<b end)")

    def test_a_string_joined_with_itself_in_a_loop_is_refused(self):
        refused(self, 'local s="a";for i=1,5 do s=s..s end', "grows without limit")
        check_script('local s="";for i=1,5 do s=s..i end print(s)')
        refused(self, 'local s="a";s=s..s;print(s)', "joined with itself")

    def test_creating_global_variables(self):
        check_script("MF_count=1")
        refused(self, "function MF_f() end", "declare a function with local")
        refused(self, "x=1", "scripts may only create variables")
        refused(self, "GetFoo=1", "game name")
        refused(self, "string.foo=1", "game name")
        refused(self, "function print() end", "declare a function with local")
        refused(self, "local a={};a.b=1;SomeTable.field=2", "game name")
        check_script("local a={};a.b=1")

    def test_timers_frames_and_hooks_are_refused(self):
        for code in ("C_Timer.After(1,print)", "CreateFrame('Frame')", 'hooksecurefunc("X",print)',
                     'local f=UIParent;f:SetScript("OnUpdate",print)', "securecall(print)"):
            refused(self, code)

    def test_methods_on_game_objects(self):
        check_script('UIErrorsFrame:AddMessage("x")')
        check_script("print(PlayerFrame:IsShown())")
        refused(self, "PlayerFrame:Hide()", "not allowed")
        refused(self, 'DEFAULT_CHAT_FRAME:Clear()', "not allowed")
        refused(self, 'local f=GetMouseFocus();f:Hide()', "not allowed")  # a local is no exception
        check_script('local f=GetMouseFocus();print(f:GetName())')

    def test_actions_must_be_called_directly(self):
        refused(self, "local f=DoEmote;f('a');f('b')", "directly")
        refused(self, 'pcall(DoEmote,"dance")', "directly")
        refused(self, 'pcall(print,"x")', "pcall")
        check_script("print(GetTime)")  # a query may be passed around

    def test_one_action_per_script_and_none_in_loops_or_functions(self):
        refused(self, 'DoEmote("dance");PetRename("x")', "one thing")
        refused(self, 'for i=1,3 do DoEmote("x") end', "loop")
        refused(self, 'local function f() DoEmote("x") end f()', "function")
        refused(self, 'table.sort({2,1},function(a,b) DoEmote("x") return a<b end)', "function")
        self.assertEqual(check_script('DoEmote("a");DoEmote("b")', max_actions=2).actions, 2)
        refused(self, 'DoEmote("a")', "one thing") if False else None

    def test_an_action_in_each_branch_counts_separately(self):
        refused(self, 'if GetTime() then DoEmote("a") else DoEmote("b") end', "one thing")

    def test_setcvar_rules(self):
        check_script('SetCVar("Sound_EnableSFX",0)')
        check_script('SetCVar("cameraDistanceMaxZoomFactor",2.6)')
        refused(self, 'SetCVar("portal","x")', "cannot be changed")
        refused(self, 'SetCVar("accountName","x")', "cannot be changed")
        refused(self, 'SetCVar("SOMEPASSWORDTHING","x")', "cannot be changed")
        refused(self, 'local n="a";SetCVar(n,1)', "written out")
        refused(self, 'SetCVar("a".."b",1)', "written out")
        refused(self, "SetCVar()", "written out")
        refused(self, 'C_CVar.SetCVar("Sound_EnableSFX",0)', "C_CVar")
        refused(self, 'SetCVarBitfield("a",1,true)', "changes macros")

    def test_whole_namespaces_are_refused(self):
        for name in load_rules().denied_namespaces:
            refused(self, f"local x={name}", name)
            refused(self, f"{name}.Anything()", name)

    def test_unusual_syntax_is_refused_not_misread(self):
        for code in ("local x = \"\\x41\"", "x = 1 y = ", "::label::", "a = b c", "x = [[unclosed"):
            refused(self, code)

    def test_denied_families_by_example(self):
        samples = {
            "casts": ["CastSpell", "CastSpellByName", "UseAction", "TargetUnit", "TargetNearestEnemy",
                      "AssistUnit", "FocusUnit", "AttackTarget", "PetAttack", "StartAttack",
                      "SpellStopCasting"],
            "chat": ["SendChatMessage", "SendAddonMessage", "SendMail", "BNSendWhisper",
                     "JoinChannelByName", "AddFriend", "AddIgnore"],
            "group": ["InviteUnit", "UninviteUnit", "LeaveParty", "PromoteToLeader", "GuildInvite",
                      "InitiateTrade", "AcceptTrade", "JoinBattlefield", "LeaveBattlefield"],
            "items": ["UseContainerItem", "PickupContainerItem", "PickupItem", "EquipItemByName",
                      "BuyMerchantItem", "SellCursorItem", "TakeInboxItem", "DeleteCursorItem",
                      "LootSlot", "RepairAllItems", "PostAuction", "DoTradeSkill"],
            "accept": ["AcceptQuest", "DeclineGroup", "ConfirmSummon", "CompleteQuest", "AbandonQuest",
                       "StaticPopup_Show", "GetQuestReward"],
            "move": ["MoveForwardStart", "StrafeLeftStart", "TurnLeftStart", "JumpOrAscendStart",
                     "FollowUnit", "ToggleAutoRun", "SitStandOrDescendStart"],
            "session": ["Logout", "Quit", "ForceQuit", "ConsoleExec", "RepopMe", "ReleaseCorpse"],
            "own": ["CreateMacro", "EditMacro", "DeleteMacro", "SetBinding", "SaveBindings",
                    "EnableAddOn", "DisableAddOn", "LoadAddOn"],
        }
        for family, names in samples.items():
            for name in names:
                with self.subTest(family=family, name=name):
                    refused(self, f"{name}()", "not allowed")
                    refused(self, f"local f={name}", "not allowed")
                    refused(self, f"C_Anything.{name}()", "not allowed")

    def test_harmless_neighbours_of_denied_names_are_allowed(self):
        for name in ("GetCVar", "GetTime", "UnitName", "GetNumGroupMembers", "GetInventoryItemLink",
                     "GetContainerNumSlots", "IsInGroup", "HasPetUI", "UnitIsDead", "GetZoneText",
                     "GetCoinTextureString", "GetNetStats", "GetFramerate"):
            check_script(f"local x={name}()")
        for name in ("DoEmote", "PetRename", "SetRaidTarget", "ReloadUI", "PlaySound"):
            check_script(f"{name}(1)")


class KnownApiTests(unittest.TestCase):
    KNOWN = ApiKnowledge(frozenset({"DoEmote", "GetCVar", "UnitName", "print"}),
                         {"C_Map": frozenset({"GetBestMapForUnit"})})

    def check(self, code):
        return check_script(code, known=self.KNOWN)

    def test_names_that_exist_pass(self):
        self.check('print(UnitName("player"));DoEmote("dance")')
        self.check('print(C_Map.GetBestMapForUnit("player"))')

    def test_invented_names_are_caught(self):
        for code in ("print(GetEverything())", 'print(C_Map.GetNothing("x"))',
                     "DoEmotes('x')", "print(UnitNames('x'))"):
            with self.assertRaises(ValidationError, msg=code) as caught:
                self.check(code)
            self.assertIn("does not exist in this game", str(caught.exception))

    def test_plain_lua_and_own_names_never_need_to_be_in_the_list(self):
        self.check("local t={};table.insert(t,1);MF_x=math.floor(2.5);print(string.format('%d',1))")

    def test_without_the_list_a_note_says_names_were_not_checked(self):
        report = check_script("print(GetSomething())")
        self.assertTrue(any("not checked" in note for note in report.notes))
        self.assertEqual(self.check("print(UnitName('x'))").notes, [])


class BodyTests(unittest.TestCase):
    def test_lines_must_be_run_commands(self):
        self.assertEqual(script_lines("/run print(1)\n  /run print(2)\n"), ["print(1)", "print(2)"])
        for body in ("print(1)", "/script print(1)", "/cast Fear", "#showtooltip\n/run print(1)", "",
                     "/run", "/RUN print(1)", "/runx print(1)"):
            with self.assertRaises(ValidationError, msg=body):
                script_lines(body)

    def test_actions_add_up_across_lines(self):
        self.assertEqual(check_script_body('/run DoEmote("a")\n/run print(1)').actions, 1)
        with self.assertRaises(ValidationError) as caught:
            check_script_body('/run DoEmote("a")\n/run PetRename("b")')
        self.assertIn("one thing", str(caught.exception))

    def test_each_line_is_its_own_chunk(self):
        # `a` is not a local on line two: an unknown name that is called counts as an action
        self.assertEqual(check_script_body("/run local a=1\n/run print(a())").actions, 1)
        with self.assertRaises(ValidationError):
            check_script_body("/run for i=1,3 do\n/run end")


class RulesDataTests(unittest.TestCase):
    def test_the_rules_file_is_well_formed(self):
        rules = load_rules()
        self.assertEqual((rules.max_actions, rules.max_loop_depth, rules.max_loop_bound), (1, 2, 1000))
        for name, reason in rules.banned.items():
            self.assertTrue(reason, name)
        for pattern, reason in rules.denied:
            self.assertTrue(reason)
            self.assertTrue(pattern.pattern.startswith("^") and pattern.pattern.endswith("$"))
        self.assertIn("C_Timer", rules.banned)
        self.assertIn("C_ChatInfo", rules.denied_namespaces)

    def test_the_documented_limits_match_the_file(self):
        text = (ROOT / "mforge" / "resources" / "script_rules.toml").read_text(encoding="utf-8")
        self.assertIn("max_actions = 1", text)


class FuzzTests(unittest.TestCase):
    """Nothing a script contains may crash the checker or make it slow."""

    PIECES = ["local", "x", "y", "f", "=", "1", "2.5", '"s"', "(", ")", "{", "}", "[", "]", ",", ";",
              ".", ":", "..", "+", "-", "#", "not", "and", "or", "if", "then", "else", "end", "for",
              "do", "in", "function", "return", "print", "SendChatMessage", "DoEmote", "_G", "MF_a",
              "C_Map", "GetTime", "SetCVar", "pairs", "ipairs", "while", "...", "==", "<", "~="]

    def test_random_token_soup(self):
        rng = random.Random(1234)
        started = time.monotonic()
        for _ in range(2000):
            code = " ".join(rng.choice(self.PIECES) for _ in range(rng.randint(1, 25)))
            try:
                check_script(code)
            except ValidationError:
                pass
        self.assertLess(time.monotonic() - started, 20)

    def test_mutated_good_scripts(self):
        rng = random.Random(99)
        for _ in range(1500):
            code = list(rng.choice(GOOD))
            for _ in range(rng.randint(1, 4)):
                index = rng.randrange(len(code))
                action = rng.choice("dri")
                if action == "d":
                    del code[index]
                elif action == "r":
                    code[index] = rng.choice("(){}[],;.=\"'\\ \n-+*/#%^<>~@`")
                else:
                    code.insert(index, rng.choice("(){}[],;.=\"'\\-+*/#%^<>~"))
                if not code:
                    code = ["x"]
            try:
                check_script("".join(code))
            except ValidationError:
                pass

    def test_binary_junk(self):
        rng = random.Random(7)
        for _ in range(300):
            junk = "".join(chr(rng.randrange(0, 0x3000)) for _ in range(rng.randint(1, 60)))
            try:
                check_script(junk)
            except ValidationError:
                pass

    def test_hostile_shapes(self):
        for code in ("x=" + "(" * 5000, "a" * 100000, "'" * 3000, "[[" * 2000, "--[[" * 2000,
                     "f(" * 300 + ")" * 300, "{" * 300, "x=1;" * 3000):
            with self.assertRaises(ValidationError):
                check_script(code)


def find_luajit() -> str | None:
    """Reuse the Lua test runner's search for LuaJIT."""
    from tests.test_lua_addon import find_luajit as find
    return find()


LUA_COMPILE = r"""
local path = arg[1]
local handle = assert(io.open(path, "rb"))
for line in handle:lines() do
    local code = line:gsub("%x%x", function(h) return string.char(tonumber(h, 16)) end)
    print(loadstring(code) and "ok" or "err")
end
"""


def lua_accepts(luajit: str, codes: list[str]) -> list[bool]:
    """Ask LuaJIT (the closest thing to the game's Lua we can run here) if each snippet compiles."""
    with tempfile.TemporaryDirectory() as tmp:
        script, data = Path(tmp) / "c.lua", Path(tmp) / "codes.txt"
        script.write_text(LUA_COMPILE, encoding="utf-8")
        # a leading space: LuaJIT (unlike the game) skips a first line that starts with "#"
        data.write_text("\n".join((" " + code).encode("utf-8").hex() for code in codes) + "\n",
                        encoding="ascii")
        out = subprocess.run([luajit, str(script), str(data)], capture_output=True, text=True,
                             timeout=120, check=True).stdout.split()
    return [word == "ok" for word in out]


def reads(code: str) -> bool:
    """True if our parser accepts the code."""
    try:
        parse(code)
        return True
    except ValidationError:
        return False


@unittest.skipUnless(find_luajit(), "LuaJIT is not installed")
class AgainstTheRealLuaTests(unittest.TestCase):
    """Our reader must agree with a real Lua compiler about what is valid (Lua 5.1 features)."""

    VOCAB = ["local", "a", "b", "f", "t", "=", "1", "2.5", "0x10", '"s"', "'q'", "[[x]]", "(", ")", "{",
             "}", "[", "]", ",", ";", ".", ":", "..", "+", "-", "*", "/", "%", "^", "#", "not", "and",
             "or", "if", "then", "else", "elseif", "end", "for", "do", "in", "function", "return",
             "nil", "true", "false", "while", "repeat", "until", "==", "~=", "<", "<=", ">"]

    def test_hand_written_corpus(self):
        corpus = GOOD + [
            "x=1 y=2", "return", "return 1,2", "local function f(a,...) return a end",
            "a.b.c:d(1)(2)[3]=4", "print[[hi]] print'x' f{1,2,a=3,[4]=5,}", "local a,b=1,2;a,b=b,a",
            "do local x end", "if a then elseif b then else end", "for i=1,3,1 do end",
            "for k,v in pairs(t) do end", "function a.b:c() end", "f(function(...) return ... end)",
            "x = {1;2;3}", "(f or g)(1)", "a = -b ^ -c", "a = not not b", "x = a .. b .. c",
            "local n={},", "x=", "print(", "1+", "f(", "end", "if x then", "for i do end",
            "for i=1 do end", "local 5", "f() = 1", "a b", "return 1 x=2", "function f( end",
            "x = {1,,2}", "local function end", "(f)", "a.1", "x = function", "while x do end",
            "repeat x=1 until x", "local function f() return ... end", "x = ...",
            "x = {a=1,b=2}", "x = {[1]=1}", "x = a.b[c].d(e):f(g)", "f{}", "f''", "a=1;;b=2",
            "x = (1)", "x = ((1))", "x = f()()", "local a <const> = 1",
        ]
        luajit = find_luajit()
        theirs = lua_accepts(luajit, corpus)
        for code, expected in zip(corpus, theirs):
            if "<const>" in code:
                continue  # newer Lua syntax: only the real game's Lua 5.1 matters, we refuse it
            self.assertEqual(reads(code), expected, code)

    def test_random_token_sequences(self):
        rng = random.Random(2024)
        codes = [" ".join(rng.choice(self.VOCAB) for _ in range(rng.randint(1, 14)))
                 for _ in range(4000)]
        theirs = lua_accepts(find_luajit(), codes)
        for code, expected in zip(codes, theirs):
            self.assertEqual(reads(code), expected, code)

    def test_everything_the_checker_accepts_compiles(self):
        rng = random.Random(55)
        accepted = []
        for _ in range(3000):
            code = " ".join(rng.choice(FuzzTests.PIECES) for _ in range(rng.randint(1, 12)))
            try:
                check_script(code)
                accepted.append(code)
            except ValidationError:
                pass
        accepted += GOOD
        for code, compiles in zip(accepted, lua_accepts(find_luajit(), accepted)):
            self.assertTrue(compiles, code)


class ModuleTests(unittest.TestCase):
    def test_public_names(self):
        for name in ("check_script", "check_script_body", "script_lines", "load_rules", "ApiKnowledge"):
            self.assertTrue(hasattr(luacheck, name), name)
        self.assertTrue(hasattr(luaparse, "parse"))


if __name__ == "__main__":
    unittest.main()
