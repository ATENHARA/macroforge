-- Behavior tests for the real addon files, run against the fake WoW API.
--   luajit tests/lua/run.lua <repo root> [<generated MacroForgeData.lua> <generated Slot.lua>]
-- Output: one "ok N name" or "not ok N name" line per test (details on "#" lines), then, when
-- generated files are given, "MACRO"/"CHAT" lines that the Python test checks.

local root = assert(arg[1], "usage: luajit run.lua <repo root>")
local stub = dofile(root .. "/tests/lua/wow_stub.lua")
local ADDON = root .. "/addon/MacroForge/"

local tests = {}
local function test(name, fn) tests[#tests + 1] = { name = name, fn = fn } end

local function eq(actual, expected, label)
    if actual ~= expected then
        error(string.format("%sexpected %s, got %s", label and (label .. ": ") or "", tostring(expected), tostring(actual)), 2)
    end
end

local function truthy(value, label) if not value then error((label or "expected a true value"), 2) end end

local function chat_has(w, text)
    for _, line in ipairs(w.chat) do if line:find(text, 1, true) then return true end end
    return false
end

local function chat_dump(w) return table.concat(w.chat, " / ") end

local function load_file(w, path)
    local fn = assert(loadfile(path))
    setfenv(fn, w.env)
    fn()
end

-- A world with the addon loaded (the data global is set first, as the .toc load order does).
local function world(opts, data)
    local w = stub.new_world(opts)
    if data ~= nil then w.env.MacroForgeData = data end
    load_file(w, ADDON .. "Codec.lua")
    load_file(w, ADDON .. "Probe.lua")
    load_file(w, ADDON .. "MacroForge.lua")
    load_file(w, ADDON .. "UI.lua")
    return w
end

local function names(list) local out = {} for _, m in ipairs(list) do out[#out + 1] = m.name end return table.concat(out, ",") end

local DATA = {
    rev = 100,
    account = { { name = "Tap", icon = 136126, body = "#showtooltip Life Tap\n/cast Life Tap" } },
    chars = { ["Testy Mage"] = { { name = "Fear MO", icon = 136183, body = "/cast [@mouseover,harm,nodead][] Fear" } } },
    remove = { account = {}, chars = {} },
    replies = {},
}

local function copy(value)
    if type(value) ~= "table" then return value end
    local out = {}
    for k, v in pairs(value) do out[k] = copy(v) end
    return out
end

local function data(extra)
    local d = copy(DATA)
    for k, v in pairs(extra or {}) do d[k] = v end
    return d
end

-- login and applying -------------------------------------------------------------------------

test("login creates account and character macros", function()
    local w = world({}, data())
    w.fire("PLAYER_LOGIN")
    eq(names(w.account), "Tap")
    eq(names(w.char), "Fear MO")
    eq(w.char[1].icon, 136183)
    truthy(chat_has(w, "2 created, 0 updated, 0 removed, 0 failed"), chat_dump(w))
end)

test("it works whether or not the client reports the per-character flag", function()
    for _, report in ipairs({ false, true }) do
        local w = world({ report_islocal = report }, data({ remove = { account = {}, chars = { ["Testy Mage"] = { "Gone" } } } }))
        w.add_macro("Gone", 1, "x", true)
        w.add_macro("Gone", 1, "x", false)  -- same name in the other scope must survive
        for _ = 1, 2 do w.add_macro("Tap", 136126, "#showtooltip Life Tap\n/cast Life Tap", false) end
        w.fire("PLAYER_LOGIN")
        eq(names(w.account), "Gone,Tap", "account macros, report_islocal=" .. tostring(report))
        eq(names(w.char), "Fear MO", "character macros, report_islocal=" .. tostring(report))
    end
end)

test("a second login with unchanged data changes nothing and prints nothing", function()
    local w = world({}, data())
    w.fire("PLAYER_LOGIN")
    w.chat = {}
    w.fire("PLAYER_LOGIN")
    eq(#w.chat, 0, "chat lines: " .. chat_dump(w))
    eq(#w.account, 1)
    eq(#w.char, 1)
end)

test("the game's trailing newline is not mistaken for a change", function()
    local w = world({}, data())
    w.add_macro("Tap", 136126, "#showtooltip Life Tap\n/cast Life Tap", false)
    w.fire("PLAYER_LOGIN")
    truthy(chat_has(w, "1 created"), chat_dump(w))  -- only the character macro
    truthy(not chat_has(w, "updated, 0 removed") or chat_has(w, "0 updated"), chat_dump(w))
end)

test("a changed body or icon updates the macro in place", function()
    local w = world({}, data())
    w.add_macro("Tap", 1, "old body", false)
    w.fire("PLAYER_LOGIN")
    eq(#w.account, 1)
    eq(w.account[1].body, "#showtooltip Life Tap\n/cast Life Tap\n")
    eq(w.account[1].icon, 136126)
    truthy(chat_has(w, "1 updated"), chat_dump(w))
end)

test("duplicates of a managed macro are removed, hand-made macros are left alone", function()
    local w = world({}, data())
    for _ = 1, 3 do w.add_macro("Tap", 136126, "#showtooltip Life Tap\n/cast Life Tap", false) end
    w.add_macro("MyHandMade", 5, "/say hi", false)
    w.fire("PLAYER_LOGIN")
    eq(names(w.account), "Tap,MyHandMade")
    truthy(chat_has(w, "2 removed"), chat_dump(w))
end)

test("the remove list deletes only the named macros in the named scope", function()
    local w = world({}, data({ remove = { account = { "Old" }, chars = { ["Testy Mage"] = { "OldChar" } } } }))
    w.add_macro("Old", 1, "x", false)
    w.add_macro("OldChar", 1, "x", true)
    w.add_macro("Old", 1, "x", true)  -- same name, other scope: must stay
    w.add_macro("Keep", 1, "x", false)
    w.fire("PLAYER_LOGIN")
    eq(names(w.account), "Keep,Tap")
    eq(names(w.char), "Old,Fear MO")
end)

test("a macro that is both applied and listed for removal is kept", function()
    local w = world({}, data({
        account = { { name = "Both", icon = 1, body = "/x" } },
        chars = { ["Testy Mage"] = { { name = "CharBoth", icon = 1, body = "/x" } } },
        remove = { account = { "Both", "Gone" }, chars = { ["Testy Mage"] = { "CharBoth", "CharGone" } } } }))
    w.add_macro("Gone", 1, "x", false)
    w.add_macro("CharGone", 1, "x", true)
    w.fire("PLAYER_LOGIN")
    eq(names(w.account), "Both")
    eq(names(w.char), "CharBoth")
end)

test("character macros are only applied to the matching character, however it is spelled", function()
    for _, key in ipairs({ "Testy Mage", "testy-mage", "TESTYMAGE", "Testy" }) do
        local w = world({}, data({ chars = { [key] = { { name = "X", icon = 1, body = "/x" } } } }))
        w.fire("PLAYER_LOGIN")
        eq(names(w.char), "X", key)
    end
    local other = world({}, data({ chars = { ["Someone Else"] = { { name = "X", icon = 1, body = "/x" } } } }))
    other.fire("PLAYER_LOGIN")
    eq(#other.char, 0)
end)

test("the surname is taken from UnitName when no other call exposes it", function()
    local w = world({ identity_only_unitname = true },
                    data({ chars = { ["Testy Mage"] = { { name = "X", icon = 1, body = "/x" } } } }))
    w.fire("PLAYER_LOGIN")
    eq(names(w.char), "X")
end)

test("an error while applying one macro is reported and the others still apply", function()
    local w = world({ edit_fails = true }, data({ account = {
        { name = "Existing", icon = 1, body = "new body" },
        { name = "After", icon = 2, body = "/cast After" } } }))
    w.add_macro("Existing", 1, "old body", false)
    local ok, err = pcall(w.fire, "PLAYER_LOGIN")
    truthy(ok, "login crashed: " .. tostring(err))
    truthy(chat_has(w, "error applying 'Existing'"), chat_dump(w))
    truthy(names(w.account):find("After", 1, true), names(w.account))
    truthy(chat_has(w, "1 failed"), chat_dump(w))
end)

test("a character without a surname still matches its first name", function()
    local w = world({ surname = "" }, data({ chars = { ["Testy"] = { { name = "X", icon = 1, body = "/x" } } } }))
    w.fire("PLAYER_LOGIN")
    eq(names(w.char), "X")
end)

test("nothing is applied in combat", function()
    local w = world({ combat = true }, data())
    w.fire("PLAYER_LOGIN")
    eq(#w.account + #w.char, 0)
    w.chat = {}
    w.env.SlashCmdList.MACROFORGE("")
    truthy(chat_has(w, "in combat"), chat_dump(w))
end)

test("slash command applies the data manually", function()
    local w = world({}, data())
    w.env.SlashCmdList.MACROFORGE("")
    eq(names(w.account), "Tap")
    truthy(chat_has(w, "2 created"), chat_dump(w))
end)

-- hostile or damaged data -----------------------------------------------------------------------

test("one malformed entry is skipped and the rest still apply", function()
    local bad = data({ account = {
        { name = string.rep("n", 70), icon = 1, body = "/x" },
        { name = "NoBody", icon = 1 },
        { name = "FloatIcon", icon = 1.5, body = "/x" },
        { name = 5, icon = 1, body = "/x" },
        "not a table",
        { name = "Good", icon = 7, body = "/cast Good" },
    } })
    local w = world({}, bad)
    w.fire("PLAYER_LOGIN")
    eq(names(w.account), "Good")
    truthy(chat_has(w, "skipped a malformed macro entry"), chat_dump(w))
    truthy(chat_has(w, "5 failed"), chat_dump(w))
end)

test("data of the wrong type never crashes the addon", function()
    local cases = { "text", 5, true, {}, { account = "x", chars = 5, remove = "y", replies = 7 },
                    { account = { 1, 2 }, chars = { Testy = 5 }, remove = { account = { 5, {} }, chars = { Testy = "z" } } },
                    { rev = "x", replies = { { id = "x" }, { id = 1 }, "s", { id = 2, text = {} } } } }
    for index, case in ipairs(cases) do
        local w = world({}, case)
        local ok, err = pcall(w.fire, "PLAYER_LOGIN")
        truthy(ok, "case " .. index .. " crashed: " .. tostring(err))
    end
end)

test("macro text and error text are shown with pipes doubled", function()
    local w = world({ create_fails = true }, data({ account = { { name = "A|cffff0000B", icon = 1, body = "/x" } } }))
    w.fire("PLAYER_LOGIN")
    truthy(chat_has(w, "A||cffff0000B"), chat_dump(w))
    for _, line in ipairs(w.chat) do
        truthy(not line:find("A|cffff0000B", 1, true), "raw escape in: " .. line)
    end
end)

test("a full macro list is reported as a failure, not a crash", function()
    local w = world({ char_max = 1 }, data({ chars = { ["Testy Mage"] = {
        { name = "One", icon = 1, body = "/x" }, { name = "Two", icon = 1, body = "/x" } } } }))
    w.fire("PLAYER_LOGIN")
    eq(names(w.char), "One")
    truthy(chat_has(w, "1 failed"), chat_dump(w))
end)

-- replies ----------------------------------------------------------------------------------------

test("replies are shown once; chat escapes in them are neutralized", function()
    local replies = { { id = 5, req = 3, text = "Done |cffff0000RED|r |Hitem:1|h[X]|h" } }
    local w = world({}, data({ replies = replies }))
    w.fire("PLAYER_LOGIN")
    local shown = 0
    for _, line in ipairs(w.chat) do
        if line:find("reply:", 1, true) then
            shown = shown + 1
            truthy(line:find("Done ||cffff0000RED||r", 1, true), line)
            truthy(not line:find("Done |cffff0000", 1, true), "raw color escape: " .. line)
        end
    end
    eq(shown, 1)
    w.chat = {}
    w.fire("PLAYER_LOGIN")
    truthy(not chat_has(w, "reply:"), "reply was shown twice")
end)

test("only newer replies are shown", function()
    local w = world({}, data({ replies = { { id = 1, req = 1, text = "old" }, { id = 2, req = 2, text = "new" } } }))
    w.env.MacroForgeSeen = 1
    w.fire("PLAYER_LOGIN")
    truthy(chat_has(w, "new") and not chat_has(w, "reply: old"), chat_dump(w))
end)

test("a reminder shows requests that no reply has answered", function()
    local w = world({}, data())
    w.env.MacroForgeRequests = { { id = 10, text = "a" }, { id = 11, text = "b" }, "junk", { id = "x" } }
    w.env.MacroForgeAnswered = 10
    w.fire("PLAYER_LOGIN")
    truthy(chat_has(w, "1 request(s) waiting"), chat_dump(w))
end)

-- /mf live -----------------------------------------------------------------------------------------

local function slot_source(rev, extra_name)
    return string.format('MacroForgeSlotData = { rev = %d, account = { { name = "%s", icon = 9, body = "/cast Live" } }, chars = {}, remove = { account = {}, chars = {} }, replies = {} }',
        rev, extra_name or "LiveMacro")
end

local function with_slots(count, rev, opts)
    local w = world(opts or {}, data())
    for i = 1, count do w.addon_files[string.format("MacroForge_S%03d", i)] = slot_source(rev) end
    w.fire("PLAYER_LOGIN")
    w.chat = {}
    return w
end

test("/mf live applies newer data from a slot without a reload", function()
    local w = with_slots(3, 200)
    w.env.SlashCmdList.MACROFORGE("live")
    truthy(names(w.account):find("LiveMacro", 1, true), names(w.account))
    truthy(chat_has(w, "1 created"), chat_dump(w))
    eq(w.reloads, 0)
    truthy(w.loaded["MacroForge_S001"] and not w.loaded["MacroForge_S002"], "only one slot is used per call")
end)

test("/mf live with unchanged data says so and still uses a slot", function()
    local w = with_slots(3, 100)  -- same rev as the login data
    w.env.SlashCmdList.MACROFORGE("live")
    truthy(chat_has(w, "no new macros (rev 100); used slot MacroForge_S001"), chat_dump(w))
    eq(#w.account, 1)
end)

test("/mf live walks through the slots and reports when they are used up", function()
    local w = with_slots(2, 200)
    w.env.SlashCmdList.MACROFORGE("live")
    w.env.SlashCmdList.MACROFORGE("live")
    w.chat = {}
    w.env.SlashCmdList.MACROFORGE("live")
    truthy(chat_has(w, "all 2 slots used"), chat_dump(w))
end)

test("/mf live without slot addons explains what to do", function()
    local w = with_slots(0, 200)
    w.env.SlashCmdList.MACROFORGE("live")
    truthy(chat_has(w, "no slot addons found"), chat_dump(w))
end)

test("a slot that leaves no data is reported", function()
    local w = world({}, data())
    w.addon_files["MacroForge_S001"] = "MacroForgeSlotData = nil"
    w.fire("PLAYER_LOGIN")
    w.chat = {}
    w.env.SlashCmdList.MACROFORGE("live")
    truthy(chat_has(w, "could not load MacroForge_S001"), chat_dump(w))
end)

test("a slot whose data is damaged does not crash /mf live", function()
    for _, source in ipairs({ "MacroForgeSlotData = 'text'", "MacroForgeSlotData = { rev = 5, account = 9 }",
                              "error('boom')", "MacroForgeSlotData = {" }) do
        local w = world({}, data())
        w.addon_files["MacroForge_S001"] = source
        w.fire("PLAYER_LOGIN")
        local ok, err = pcall(w.env.SlashCmdList.MACROFORGE, "live")
        truthy(ok, source .. " crashed: " .. tostring(err))
    end
end)

test("/mf live is refused in combat", function()
    local w = with_slots(1, 200, { combat = true })
    w.env.SlashCmdList.MACROFORGE("live")
    truthy(chat_has(w, "in combat"), chat_dump(w))
    eq(next(w.loaded), nil)
end)

-- /mf ask --------------------------------------------------------------------------------------------

local function count_loaded(w)
    local n = 0
    for _ in pairs(w.loaded) do n = n + 1 end
    return n
end

local function slot_with_reply(rev, req)
    return string.format('MacroForgeSlotData = { rev = %d, account = { { name = "FromAsk", icon = 9, body = "/cast Ask" } }, chars = {}, remove = { account = {}, chars = {} }, replies = { { id = 1, req = %d, text = "done" } } }',
        rev, req)
end

test("/mf ask saves the request, sends it as a screenshot and restores the format", function()
    local w = world({ spells = { { name = "Fear", spellID = 5782, isPassive = false } } }, data())
    w.fire("PLAYER_LOGIN")
    w.env.SlashCmdList.MACROFORGE("ask make me a fear macro")
    local req = w.env.MacroForgeRequests[1]
    eq(req.text, "make me a fear macro")
    eq(req.char, "Testy Mage")
    eq(req.class, "WARLOCK")
    eq(req.level, 30)
    eq(type(req.id), "number")
    eq(w.env.MacroForgeExport.spells[1].name, "Fear")
    eq(w.reloads, 0)
    eq(w.shots, 0)  -- the picture is taken after a short wait so the squares can render
    w.advance(0.6)
    eq(w.shots, 1)
    eq(w.formats_at_shot[1], "tga")
    w.advance(2)
    eq(w.cvars.screenshotFormat, "jpeg")
    truthy(chat_has(w, "Request sent"), chat_dump(w))
end)

test("/mf ask ids increase, the list is capped, and bad input is refused", function()
    local w = world({}, data())
    for i = 1, 12 do w.env.SlashCmdList.MACROFORGE("ask request " .. i) end
    eq(#w.env.MacroForgeRequests, 10)
    for i = 2, 10 do truthy(w.env.MacroForgeRequests[i].id > w.env.MacroForgeRequests[i - 1].id, "ids must increase") end
    w.chat = {}
    w.env.SlashCmdList.MACROFORGE("ask")
    truthy(chat_has(w, "usage"), chat_dump(w))
    w.env.SlashCmdList.MACROFORGE("ask " .. string.rep("x", 501))
    truthy(chat_has(w, "too long"), chat_dump(w))
    eq(#w.env.MacroForgeRequests, 10)
end)

test("/mf ask survives a damaged request list", function()
    local w = world({}, data())
    for _, bad in ipairs({ "text", 5, { "x", { id = "y" } } }) do
        w.env.MacroForgeRequests = bad
        local ok, err = pcall(w.env.SlashCmdList.MACROFORGE, "ask hello")
        truthy(ok, "crashed on " .. tostring(bad) .. ": " .. tostring(err))
        eq(type(w.env.MacroForgeRequests), "table")
    end
end)

test("/mf ask reports a failed screenshot and does not wait for a reply", function()
    local w = world({ screenshot_fails = true }, data())
    w.env.SlashCmdList.MACROFORGE("ask hello")
    w.advance(3)
    truthy(chat_has(w, "could not take the screenshot"), chat_dump(w))
    eq(#w.timers, 0)
end)

test("/mf ask keeps a request to one line (tabs and newlines would break the payload)", function()
    local w = world({}, data())
    w.env.SlashCmdList.MACROFORGE("ask first\tsecond\nthird")
    w.advance(3)
    eq(w.shots, 1)
end)

test("after /mf ask the addon polls spare slots, applies the reply and plays a sound", function()
    local w = world({}, data())
    for i = 1, 5 do w.addon_files[string.format("MacroForge_S%03d", i)] = slot_source(150) end
    w.fire("PLAYER_LOGIN")
    w.env.SlashCmdList.MACROFORGE("ask make a macro")
    local id = w.env.MacroForgeRequests[1].id
    w.advance(2.2)      -- screenshot taken, polling starts
    w.advance(11)
    eq(count_loaded(w), 0)  -- the first check is at 12 s
    w.advance(2)
    eq(count_loaded(w), 1)  -- no reply yet: slot used, keep waiting
    truthy(chat_has(w, "has not picked up"), chat_dump(w))
    eq(#w.sounds, 0)
    w.addon_files["MacroForge_S002"] = slot_with_reply(300, id)
    w.advance(10)
    truthy(names(w.account):find("FromAsk", 1, true), names(w.account))
    truthy(chat_has(w, "|r done"), chat_dump(w))
    eq(#w.sounds, 1)
    w.advance(120)
    eq(count_loaded(w), 2)  -- polling stopped after the reply
end)

test("an acknowledgement from the watcher is shown but does not apply anything", function()
    local w = world({}, data())
    w.fire("PLAYER_LOGIN")
    w.env.SlashCmdList.MACROFORGE("ask make a macro")
    local id = w.env.MacroForgeRequests[1].id
    w.addon_files["MacroForge_S001"] = string.format('MacroForgeSlotData = { rev = 300, account = { { name = "Early", icon = 9, body = "/cast X" } }, chars = {}, remove = { account = {}, chars = {} }, replies = { { id = 1, req = %d, text = "Got it", pending = true } } }', id)
    w.advance(15)
    truthy(chat_has(w, "working on it"), chat_dump(w))
    truthy(not chat_has(w, "has not picked up"), chat_dump(w))
    eq(#w.sounds, 0)
    truthy(not names(w.account):find("Early", 1, true), names(w.account))
    eq(w.env.MacroForgeAnswered or 0, 0)
end)

test("polling gives up after three minutes and says how to fetch the reply later", function()
    local w = world({}, data())
    for i = 1, 40 do w.addon_files[string.format("MacroForge_S%03d", i)] = slot_source(150) end
    w.fire("PLAYER_LOGIN")
    w.env.SlashCmdList.MACROFORGE("ask make a macro")
    w.advance(400)
    truthy(chat_has(w, "Still no answer"), chat_dump(w))
    truthy(count_loaded(w) >= 10 and count_loaded(w) <= 20, "slots used: " .. count_loaded(w))
    eq(#w.sounds, 0)
end)

test("a newer request replaces the poll of an older one", function()
    local w = world({}, data())
    for i = 1, 40 do w.addon_files[string.format("MacroForge_S%03d", i)] = slot_source(150) end
    w.fire("PLAYER_LOGIN")
    w.env.SlashCmdList.MACROFORGE("ask first")
    w.env.SlashCmdList.MACROFORGE("ask second")
    w.advance(15)
    eq(count_loaded(w), 1)  -- only one chain of checks is running
end)

test("polling waits out combat instead of loading slots", function()
    local w = world({ combat = true }, data())
    w.addon_files["MacroForge_S001"] = slot_source(150)
    w.env.SlashCmdList.MACROFORGE("ask hello")
    w.advance(60)
    eq(count_loaded(w), 0)
end)

test("polling tells the user when there are no slots", function()
    local w = world({}, data())
    w.env.SlashCmdList.MACROFORGE("ask hello")
    w.advance(30)
    truthy(chat_has(w, "no slot addons found"), chat_dump(w))
end)

test("the addon never sends chat messages", function()
    local w = world({}, data())
    w.env.SendChatMessage = function() error("the addon must not send chat messages") end
    w.fire("PLAYER_LOGIN")
    for _, command in ipairs({ "", "live", "export", "probe", "petcheck", "ask hello" }) do
        w.env.SlashCmdList.MACROFORGE(command)
    end
end)

-- export, probe and petcheck -------------------------------------------------------------------------

test("/mf export records spells, macros, pet and identity", function()
    local w = world({ spells = { { name = "Fear", spellID = 5782, isPassive = false }, { name = "Dodge", spellID = 81, isPassive = true } },
                      pet = { name = "Pet", family = "Imp", actions = { "Firebolt", "Fire Shield" } } }, data())
    w.add_macro("Mine", 1, "/x", true)
    w.env.SlashCmdList.MACROFORGE("export")
    local export = w.env.MacroForgeExport
    eq(#export.spells, 2)
    eq(export.pet.family, "Imp")
    eq(export.pet.actions[2], "Fire Shield")
    eq(export.macros[1].name, "Mine")
    eq(export.macros[1].isLocal, true)
    eq(export.class, "WARLOCK")
    truthy(chat_has(w, "exported 2 spells"), chat_dump(w))
end)

test("/mf probe classifies conditionals the way the parser answers", function()
    local w = world({}, data())
    w.env.SlashCmdList.MACROFORGE("probe")
    local probe = w.env.MacroForgeProbe
    eq(probe.build.toc, 16001)
    eq(probe.control.both, "Y", "an unknown conditional is ignored, so [X,noX] stays true")
    eq(probe.conditionals["harm"].both, "N", "a real conditional cannot be both true and false")
    eq(probe.conditionals["advflyable"].both, "Y", "unknown to this fake client")
    truthy(#probe.slash >= 1 and probe.slash[1]:sub(1, 1) == "/", "slash commands are collected from SLASH_ globals")
    eq(probe.api["CreateMacro"], "function")
end)

test("/mf probe's [X,noX] test works even when the conditional is true right now", function()
    local w = world({ conditions = { combat = true } }, data())
    w.env.SlashCmdList.MACROFORGE("probe")
    local combat = w.env.MacroForgeProbe.conditionals["combat"]
    eq(combat.pos, "Y", "[combat] is true in this world")
    eq(combat.neg, "N")
    eq(combat.both, "N", "[combat,nocombat] can never be true for a real conditional")
end)

test("/mf petcheck explains a missing pet, and prints the checks when there is one", function()
    local w = world({}, data())
    w.env.SlashCmdList.MACROFORGE("petcheck")
    truthy(chat_has(w, "no pet out"), chat_dump(w))
    local w2 = world({ pet = { name = "Pet", family = "Imp" }, target = "Will" }, data())
    w2.env.SlashCmdList.MACROFORGE("petcheck")
    truthy(chat_has(w2, "family=Imp"), chat_dump(w2))
    truthy(chat_has(w2, "[pet:Imp]"), chat_dump(w2))
end)

-- minimap button and window --------------------------------------------------------------------------

local function click(widget, which) widget.scripts.OnClick(widget, which or "LeftButton") end

local function ui_world(opts, data_table)
    local w = world(opts or {}, data_table or data())
    w.fire("PLAYER_LOGIN")
    return w
end

local function window_of(w) return w.env.MacroForgeWindow end

test("login creates a minimap button that opens and closes the window", function()
    local w = ui_world()
    local button = w.env.MacroForgeMinimapButton
    truthy(button and button.shown, "no minimap button")
    eq(window_of(w), nil)  -- the window is built on first use
    click(button)
    truthy(window_of(w).shown, "window did not open")
    click(button)
    truthy(not window_of(w).shown, "window did not close")
    eq(w.env.UISpecialFrames[1], "MacroForgeWindow")  -- Esc closes it
end)

test("a hidden-button setting is respected, and /mf button brings it back", function()
    local w = world({}, data())
    w.env.MacroForgeUI = { hidden = true }
    w.fire("PLAYER_LOGIN")
    truthy(not w.env.MacroForgeMinimapButton.shown, "button should be hidden")
    w.env.SlashCmdList.MACROFORGE("button")
    truthy(w.env.MacroForgeMinimapButton.shown, "button should be back")
    eq(w.env.MacroForgeUI.hidden, nil)
    w.env.SlashCmdList.MACROFORGE("button")
    truthy(not w.env.MacroForgeMinimapButton.shown)
    truthy(chat_has(w, "/mf button"), chat_dump(w))
end)

test("/mf ui toggles the window", function()
    local w = ui_world()
    w.env.SlashCmdList.MACROFORGE("ui")
    truthy(window_of(w).shown)
    w.env.SlashCmdList.MACROFORGE("ui")
    truthy(not window_of(w).shown)
end)

test("the button tooltip explains the clicks", function()
    local w = ui_world()
    local button = w.env.MacroForgeMinimapButton
    button.scripts.OnEnter(button)
    local text = table.concat(w.tooltip, "|")
    truthy(text:find("Left-click", 1, true) and text:find("Right-click", 1, true), text)
end)

test("right-click opens the window with the advanced options showing", function()
    local w = ui_world()
    click(w.env.MacroForgeMinimapButton, "RightButton")
    local window = window_of(w)
    truthy(window.shown and window.expanded and window.advanced.shown, "advanced not shown")
    click(w.find("Button", "Advanced  <<"))
    truthy(not window.advanced.shown, "advanced did not collapse")
end)

test("Send turns the request into an ask and clears the box", function()
    local w = ui_world()
    click(w.env.MacroForgeMinimapButton)
    local window = window_of(w)
    window.edit:SetText("make a  fear\nmacro")
    click(w.find("Button", "Send request"))
    eq(#w.env.MacroForgeRequests, 1)
    eq(w.env.MacroForgeRequests[1].text, "make a fear macro")  -- one line, single spaces
    eq(window.edit:GetText(), "")
    truthy(window.status:GetText():find("Sending", 1, true), window.status:GetText())
    w.advance(2.2)
    truthy(window.status:GetText():find("Request sent", 1, true), window.status:GetText())
end)

test("Send with an empty box asks for text and sends nothing", function()
    local w = ui_world()
    click(w.env.MacroForgeMinimapButton)
    window_of(w).edit:SetText("   ")
    click(w.find("Button", "Send request"))
    eq(w.env.MacroForgeRequests, nil)
    truthy(window_of(w).status:GetText():find("Type what you want", 1, true))
end)

test("a refused ask keeps the text in the box", function()
    local w = ui_world()
    click(w.env.MacroForgeMinimapButton)
    local window = window_of(w)
    window.edit:SetText(string.rep("x", 501))
    click(w.find("Button", "Send request"))
    eq(#window.edit:GetText(), 501)
    truthy(window.status:GetText():find("too long", 1, true), window.status:GetText())
end)

test("the character counter follows the text", function()
    local w = ui_world()
    click(w.env.MacroForgeMinimapButton)
    local window = window_of(w)
    window.edit:SetText("hello")
    window.edit.scripts.OnTextChanged(window.edit)
    truthy(w.find("FontString", "5/500"), "counter not updated")
end)

test("Apply new macros loads a slot and shows the result", function()
    local w = world({}, data())
    w.addon_files["MacroForge_S001"] = slot_source(300)
    w.fire("PLAYER_LOGIN")
    click(w.env.MacroForgeMinimapButton)
    click(w.find("Button", "Apply new macros"))
    truthy(names(w.account):find("LiveMacro", 1, true), names(w.account))
    local w2 = ui_world()
    click(w2.env.MacroForgeMinimapButton)
    click(w2.find("Button", "Apply new macros"))
    truthy(window_of(w2).status:GetText():find("no slot addons", 1, true), window_of(w2).status:GetText())
end)

test("the advanced buttons run export, probe and reload", function()
    local w = ui_world({ spells = { { name = "Fear", spellID = 5782, isPassive = false } } })
    click(w.env.MacroForgeMinimapButton, "RightButton")
    click(w.find("Button", "Export spellbook"))
    eq(w.env.MacroForgeExport.spells[1].name, "Fear")
    click(w.find("Button", "Probe this client"))
    truthy(w.env.MacroForgeProbe ~= nil, "probe did not run")
    eq(w.find("Button", "Pet check"), nil)  -- a developer command only, not in the window
    click(w.find("Button", "Reload UI"))
    eq(w.reloads, 1)
end)

test("the minimap button follows the mouse around the minimap while dragged", function()
    local w = ui_world()
    local button = w.env.MacroForgeMinimapButton
    button.scripts.OnDragStart(button)
    w.cursor = { x = 700, y = 500 }  -- to the right of the center (500, 500)
    button.scripts.OnUpdate(button)
    eq(w.env.MacroForgeUI.angle, 0)
    w.cursor = { x = 500, y = 900 }  -- straight above
    button.scripts.OnUpdate(button)
    eq(w.env.MacroForgeUI.angle, 90)
    w.cursor = { x = 300, y = 500 }  -- to the left
    button.scripts.OnUpdate(button)
    eq(w.env.MacroForgeUI.angle, 180)
    button.scripts.OnDragStop(button)
    eq(button.scripts.OnUpdate, nil)
    eq(button.props.Point[1], "CENTER")
end)

test("the saved angle is used when the button is created", function()
    local w = world({}, data())
    w.env.MacroForgeUI = { angle = 90 }
    w.fire("PLAYER_LOGIN")
    local point = w.env.MacroForgeMinimapButton.props.Point
    truthy(math.abs(point[4]) < 0.001 and point[5] > 0, "button should sit above the center")
end)

test("the window checkbox hides and shows the minimap button", function()
    local w = ui_world()
    click(w.env.MacroForgeMinimapButton, "RightButton")
    local box = window_of(w).showButton
    truthy(box:GetChecked(), "should start checked")
    click(box)
    truthy(not w.env.MacroForgeMinimapButton.shown)
    eq(w.env.MacroForgeUI.hidden, true)
    click(box)
    truthy(w.env.MacroForgeMinimapButton.shown)
end)

test("the window still opens when this client lacks the Blizzard templates", function()
    local w = ui_world({ fail_templates = true })
    click(w.env.MacroForgeMinimapButton)
    truthy(window_of(w).shown)
    click(w.env.MacroForgeMinimapButton, "RightButton")
    truthy(window_of(w).advanced.shown)
end)

test("a damaged saved-settings value does not break the button", function()
    for _, bad in ipairs({ "text", 5, true }) do
        local w = world({}, data())
        w.env.MacroForgeUI = bad
        w.fire("PLAYER_LOGIN")
        truthy(w.env.MacroForgeMinimapButton ~= nil)
    end
end)

test("the finished reply updates the window status", function()
    local w = world({}, data())
    for i = 1, 3 do w.addon_files[string.format("MacroForge_S%03d", i)] = slot_source(150) end
    w.fire("PLAYER_LOGIN")
    click(w.env.MacroForgeMinimapButton)
    window_of(w).edit:SetText("make a macro")
    click(w.find("Button", "Send request"))
    local id = w.env.MacroForgeRequests[1].id
    w.advance(2.2)
    w.addon_files["MacroForge_S002"] = slot_with_reply(300, id)
    w.advance(30)
    truthy(window_of(w).status:GetText():find("Done", 1, true), window_of(w).status:GetText())
    eq(#w.sounds, 1)
end)

test("every label, button and line of the window sits inside the window", function()
    local w = ui_world()
    click(w.env.MacroForgeMinimapButton, "RightButton")
    local window = window_of(w)
    local checked = 0
    for _, obj in ipairs(w.widgets) do
        local point = obj.props.Point
        if point and obj ~= window then
            local x, y, node = 0, 0, obj
            while node and node ~= window do
                local p = node.props.Point
                if p then x, y = x + (p[4] or 0), y + (p[5] or 0) end
                node = rawget(node, "parent")
            end
            if node == window then  -- skip anything that is not part of the window
                checked = checked + 1
                local what = tostring(obj.kind) .. " " .. tostring(obj.props.Text)
                truthy(type(point[4]) == "number" and type(point[5]) == "number", "bad offsets: " .. what)
                truthy(x >= 0 and x < 380, "outside horizontally (" .. x .. "): " .. what)
                truthy(y <= 0 and y > -600, "outside vertically (" .. y .. "): " .. what)
            end
        end
    end
    truthy(checked >= 15, "only checked " .. checked)
end)

test("left-click opens the simple view even after the advanced view was used", function()
    local w = ui_world()
    local button = w.env.MacroForgeMinimapButton
    click(button, "RightButton")
    truthy(window_of(w).expanded)
    click(button)  -- closes it
    click(button)  -- opens it again
    local window = window_of(w)
    truthy(window.shown and not window.expanded and not window.advanced.shown, "should be the simple view")
    w.env.SlashCmdList.MACROFORGE("ui")  -- closes
    w.env.SlashCmdList.MACROFORGE("ui")  -- opens
    truthy(not window.expanded)
end)

test("several new macros in one reply are all created, account-wide and per character", function()
    local w = world({}, data())
    w.fire("PLAYER_LOGIN")
    w.chat = {}
    local burst = data({
        rev = 900,
        account = { { name = "A1", icon = 1, body = "/x" }, { name = "A2", icon = 1, body = "/x" } },
        chars = { ["Testy Mage"] = { { name = "C1", icon = 1, body = "/x" },
                                      { name = "C2", icon = 1, body = "/x" },
                                      { name = "C3", icon = 1, body = "/x" } } },
    })
    w.addon_files["MacroForge_S001"] = "MacroForgeSlotData = " .. (function(t)
        local function ser(v)
            if type(v) == "table" then
                local parts = {}
                for k, x in pairs(v) do parts[#parts + 1] = "[" .. ser(k) .. "]=" .. ser(x) end
                return "{" .. table.concat(parts, ",") .. "}"
            end
            return type(v) == "string" and string.format("%q", v) or tostring(v)
        end
        return ser(t)
    end)(burst)
    w.env.SlashCmdList.MACROFORGE("live")
    truthy(chat_has(w, "5 created"), chat_dump(w))
    truthy(names(w.account):find("A1", 1, true) and names(w.account):find("A2", 1, true), names(w.account))
    eq(names(w.char):find("C1", 1, true) ~= nil and names(w.char):find("C3", 1, true) ~= nil, true)
end)

-- run ---------------------------------------------------------------------------------------------------

local failures = 0
for index, t in ipairs(tests) do
    local ok, err = pcall(t.fn)
    if ok then
        print(string.format("ok %d %s", index, t.name))
    else
        failures = failures + 1
        print(string.format("not ok %d %s", index, t.name))
        print("  # " .. tostring(err):gsub("\n", "\n  # "))
    end
end
print(string.format("# %d tests, %d failed", #tests, failures))

-- Optional: apply files generated by the Python tool and report what the game would end up with.
local data_file, slot_file = arg[2], arg[3]
if data_file then
    local w = stub.new_world({})
    load_file(w, data_file)
    load_file(w, ADDON .. "Codec.lua")
    load_file(w, ADDON .. "Probe.lua")
    load_file(w, ADDON .. "MacroForge.lua")
    load_file(w, ADDON .. "UI.lua")
    if slot_file then
        local handle = assert(io.open(slot_file, "rb"))
        w.addon_files["MacroForge_S001"] = handle:read("*a")
        handle:close()
    end
    w.fire("PLAYER_LOGIN")
    for _, line in ipairs(w.chat) do print("LOGIN\t" .. line) end
    local function hex(s) return (s:gsub(".", function(c) return string.format("%02x", c:byte()) end)) end
    for _, scope in ipairs({ "account", "char" }) do
        for _, m in ipairs(w[scope]) do
            print(string.format("MACRO\t%s\t%s\t%s\t%d", scope, hex(m.name), hex((m.body:gsub("\n$", ""))), m.icon))
        end
    end
    w.chat = {}
    w.env.SlashCmdList.MACROFORGE("live")
    for _, line in ipairs(w.chat) do print("CHAT\t" .. line) end
end

os.exit(failures == 0 and 0 or 1)
