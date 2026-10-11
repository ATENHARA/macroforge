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

-- scripts: request mode, the window toggle, the syntax check, the Blizzard notice, the probe ---------------

local function spy_strip(w)
    local seen = {}
    w.env.MacroForgeStrip.Send = function(id, payload, done) seen.payload, seen.id, seen.done = payload, id, done end
    return seen
end

local function fields(payload)
    local out = {}
    for part in (payload .. "\t"):gmatch("(.-)\t") do out[#out + 1] = part end
    return out
end

local function script_data(body, extra)
    local entry = { name = "Coords", icon = 1, body = body, script = true }
    for k, v in pairs(extra or {}) do entry[k] = v end
    return data({ account = { entry } })
end

test("/mf ask keeps the old six-field payload and records the macro mode", function()
    local w = ui_world()
    local seen = spy_strip(w)
    w.env.SlashCmdList.MACROFORGE("ask make a fear macro")
    eq(#fields(seen.payload), 6)
    eq(w.env.MacroForgeRequests[1].mode, "macro")
end)

test("/mf script sends a script request with a seventh field", function()
    local w = ui_world()
    local seen = spy_strip(w)
    w.env.SlashCmdList.MACROFORGE("script print my coordinates")
    local parts = fields(seen.payload)
    eq(#parts, 7)
    eq(parts[7], "script")
    eq(parts[6], "print my coordinates")
    eq(w.env.MacroForgeRequests[1].mode, "script")
    truthy(chat_has(w, "script request"), chat_dump(w))
end)

test("the usage messages name the right command", function()
    local w = ui_world()
    w.env.SlashCmdList.MACROFORGE("script")
    truthy(chat_has(w, "usage: /mf script"), chat_dump(w))
    w.chat = {}
    w.env.SlashCmdList.MACROFORGE("ask")
    truthy(chat_has(w, "usage: /mf ask"), chat_dump(w))
    eq(w.env.MacroForgeRequests, nil)
end)

test("a finished script request says the script is ready", function()
    local w = ui_world()
    w.addon_files["MacroForge_S001"] = slot_source(150)
    w.env.SlashCmdList.MACROFORGE("script print my coordinates")
    click(w.env.MacroForgeMinimapButton)
    local id = w.env.MacroForgeRequests[1].id
    w.advance(2.2)
    w.addon_files["MacroForge_S002"] = slot_with_reply(300, id)
    w.advance(30)
    truthy(window_of(w).status:GetText():find("Your script is ready", 1, true), window_of(w).status:GetText())
end)

test("the window switches between macro and script requests", function()
    local w = ui_world()
    click(w.env.MacroForgeMinimapButton)
    local window = window_of(w)
    eq(window.mode, "macro")
    eq(window.heading:GetText(), "Ask for a macro")
    click(w.find("Button", "Script"))
    eq(window.mode, "script")
    eq(window.heading:GetText(), "Ask for a script")
    truthy(window.desc:GetText():find("script", 1, true))
    truthy(window.status:GetText():find("custom-script warning", 1, true), window.status:GetText())
    eq(w.find("Button", "Script").props.Disabled, true)   -- the active mode looks pressed
    eq(w.find("Button", "Macro").props.Disabled, false)
    local seen = spy_strip(w)
    window.edit:SetText("toggle the sound effects")
    click(w.find("Button", "Send request"))
    eq(fields(seen.payload)[7], "script")
    click(w.find("Button", "Macro"))
    eq(window.mode, "macro")
    eq(window.heading:GetText(), "Ask for a macro")
    window.edit:SetText("a fear macro")
    click(w.find("Button", "Send request"))
    eq(#fields(seen.payload), 6)
end)

test("switching back to macro mode restores the idle status", function()
    local w = ui_world()
    click(w.env.MacroForgeMinimapButton)
    click(w.find("Button", "Script"))
    click(w.find("Button", "Macro"))
    eq(window_of(w).status:GetText(), "Ready.")
end)

test("the chosen mode is remembered between sessions", function()
    local w = ui_world()
    click(w.env.MacroForgeMinimapButton)
    click(w.find("Button", "Script"))
    eq(w.env.MacroForgeUI.mode, "script")
    local w2 = world({}, data())
    w2.env.MacroForgeUI = { mode = "script" }
    w2.fire("PLAYER_LOGIN")
    click(w2.env.MacroForgeMinimapButton)
    eq(window_of(w2).heading:GetText(), "Ask for a script")
    local w3 = world({}, data())
    w3.env.MacroForgeUI = { mode = "bogus" }
    w3.fire("PLAYER_LOGIN")
    click(w3.env.MacroForgeMinimapButton)
    eq(window_of(w3).mode, "macro")
end)

test("a script macro is installed and the Blizzard warning is explained once", function()
    local w = world({}, script_data('/run print("hi")'))
    w.fire("PLAYER_LOGIN")
    eq(names(w.account), "Coords")
    truthy(chat_has(w, "Blizzard shows a warning"), chat_dump(w))
    truthy(chat_has(w, "appears for every custom script"), chat_dump(w))
    eq(w.env.MacroForgeUI.scriptNoticeSeen, true)
    w.chat = {}
    w.env.SlashCmdList.MACROFORGE("")
    truthy(not chat_has(w, "Blizzard shows a warning"), chat_dump(w))
    w.addon_files["MacroForge_S001"] = (function()
        return 'MacroForgeSlotData = { rev = 400, account = { { name = "Coords", icon = 1, body = "/run print(2)", script = true } }, chars = {}, remove = { account = {}, chars = {} }, replies = {} }'
    end)()
    w.env.SlashCmdList.MACROFORGE("live")
    truthy(not chat_has(w, "Blizzard shows a warning"), "the notice must not repeat")
end)

test("the notice is skipped once Blizzard's warning has been accepted", function()
    local w = world({}, script_data('/run print("hi")'))
    w.cvars.allowDangerousScripts = "1"
    w.fire("PLAYER_LOGIN")
    eq(names(w.account), "Coords")
    truthy(not chat_has(w, "Blizzard shows a warning"), chat_dump(w))
    local w2 = world({}, script_data('/run print("hi")'))
    w2.cvars.allowDangerousScripts = "0"
    w2.fire("PLAYER_LOGIN")
    truthy(chat_has(w2, "Blizzard shows a warning"), chat_dump(w2))
end)

test("a client without that setting still gets the notice", function()
    local w = world({}, script_data('/run print("hi")'))
    w.env.GetCVar = function() error("unknown cvar") end
    w.fire("PLAYER_LOGIN")
    truthy(chat_has(w, "Blizzard shows a warning"), chat_dump(w))
end)

test("ordinary macros never trigger the script notice", function()
    local w = world({}, data())
    w.fire("PLAYER_LOGIN")
    truthy(not chat_has(w, "Blizzard"), chat_dump(w))
    eq((w.env.MacroForgeUI or {}).scriptNoticeSeen, nil)
end)

test("a script with a syntax error is not installed, and the others still are", function()
    local d = data({ account = { { name = "Bad", icon = 1, body = "/run print(", script = true },
                                 { name = "Good", icon = 1, body = "/run print(1)", script = true },
                                 { name = "Plain", icon = 1, body = "/cast Fear" } } })
    local w = world({}, d)
    w.fire("PLAYER_LOGIN")
    eq(names(w.account), "Good,Plain")
    truthy(chat_has(w, "'Bad' was not installed"), chat_dump(w))
    truthy(chat_has(w, "1 failed") or chat_has(w, "2 created, 0 updated, 0 removed, 1 failed"), chat_dump(w))
end)

test("a script entry must be /run lines only", function()
    for _, body in ipairs({ "/cast Fear", "print(1)", "#showtooltip\n/run print(1)", "/run" }) do
        local w = world({}, script_data(body))
        w.fire("PLAYER_LOGIN")
        eq(#w.account, 0)
        truthy(chat_has(w, "was not installed"), body .. " / " .. chat_dump(w))
    end
end)

test("several /run lines are each checked", function()
    local w = world({}, script_data("/run print(1)\n/run print(2"))
    w.fire("PLAYER_LOGIN")
    eq(#w.account, 0)
    local w2 = world({}, script_data("/run print(1)\n/run print(2)"))
    w2.fire("PLAYER_LOGIN")
    eq(#w2.account, 1)
end)

test("a script is never run by MacroForge, only compiled", function()
    local ran = false
    local w = world({}, script_data("/run MF_RAN=true"))
    w.env.MF_RAN = nil
    w.fire("PLAYER_LOGIN")
    eq(#w.account, 1)
    eq(w.env.MF_RAN, nil)
end)

test("a script flag of the wrong type makes the entry malformed", function()
    local w = world({}, script_data("/run print(1)", { script = "yes" }))
    w.fire("PLAYER_LOGIN")
    eq(#w.account, 0)
    truthy(chat_has(w, "malformed"), chat_dump(w))
end)

test("without loadstring the syntax check is skipped but the /run rule still applies", function()
    local w = world({}, script_data("/run print("))
    w.env.loadstring = false
    w.fire("PLAYER_LOGIN")
    eq(#w.account, 1)
    local w2 = world({}, script_data("/cast Fear"))
    w2.env.loadstring = false
    w2.fire("PLAYER_LOGIN")
    eq(#w2.account, 0)
end)

test("the probe records the game's own functions and leaves other addons out", function()
    local w = world({}, data())
    w.env.GetTime = function() return 1 end
    w.env.C_Map = { GetBestMapForUnit = function() end, Version = 1 }
    w.env.UIErrorsFrame = {}
    w.env.EvilAddonGlobal = function() end
    w.env.SomeAddonTable = {}
    w.env.issecurevariable = function(name) return name ~= "EvilAddonGlobal" and name ~= "SomeAddonTable" end
    w.env.MacroForge_Probe()
    local script = w.env.MacroForgeProbe.script
    local set = {}
    for _, name in ipairs(script.names) do set[name] = true end
    truthy(set.GetTime and set.C_Map and set.UIErrorsFrame, "game names are recorded")
    truthy(not set.EvilAddonGlobal and not set.SomeAddonTable, "another addon's names must not be recorded")
    eq(script.members.C_Map[1], "GetBestMapForUnit")
    eq(#script.members.C_Map, 1)
    eq(script.loadstring, "function")
    local sorted = true
    for i = 2, #script.names do if script.names[i - 1] > script.names[i] then sorted = false end end
    truthy(sorted, "names are sorted")
    truthy(chat_has(w, "game functions for scripts"), chat_dump(w))
end)

test("the probe copes with a client that has no issecurevariable", function()
    local w = world({}, data())
    w.env.GetTime = function() return 1 end
    w.env.issecurevariable = nil
    local ok, err = pcall(w.env.MacroForge_Probe)
    truthy(ok, tostring(err))
    eq(#w.env.MacroForgeProbe.script.names, 0)  -- unknown whose names these are: record none
end)

-- ideas and history ---------------------------------------------------------------------------------------

local IDEAS = {
    macros = {
        WARLOCK = {
            { name = "Mouseover Fear", icon = 136183, level = 8, why = "Fears the unit under your mouse.",
              body = "#showtooltip\n/cast [@mouseover,harm,nodead][] Fear" },
            { name = "Curse Picker", icon = 136126, level = 20, why = "One button for curses.",
              body = "#showtooltip\n/cast [mod:shift] Curse of Weakness; Curse of the Elements" },
        },
        MAGE = { { name = "Mouseover Poly", icon = 136100, level = 8, why = "Polymorph the unit under your mouse.",
                   body = "#showtooltip\n/cast [@mouseover,harm,nodead][] Polymorph" } },
    },
    scripts = {
        { name = "Coords", icon = 134269, level = 0, why = "Prints where you are.", script = true,
          body = '/run print(GetZoneText())' },
    },
}

local function ideas_world(opts, suggestions)
    local w = world(opts or {}, data())
    if suggestions ~= false then w.env.MacroForgeSuggestions = suggestions or copy(IDEAS) end
    w.fire("PLAYER_LOGIN")
    w.chat = {}
    return w
end

local function idea_names(list)
    local out = {}
    for _, e in ipairs(list) do out[#out + 1] = e.name end
    return table.concat(out, ",")
end

test("macro ideas are the ones for this character's class", function()
    local w = ideas_world()
    eq(idea_names(w.env.MacroForgeCore.ideas("macro")), "Mouseover Fear,Curse Picker")
    local mage = ideas_world({ class = "MAGE", class_name = "Mage" })
    eq(idea_names(mage.env.MacroForgeCore.ideas("macro")), "Mouseover Poly")
    local other = ideas_world({ class = "ROGUE", class_name = "Rogue" })
    eq(#other.env.MacroForgeCore.ideas("macro"), 0)
end)

test("script examples are the same for every class", function()
    for _, class in ipairs({ "WARLOCK", "ROGUE" }) do
        local w = ideas_world({ class = class })
        eq(idea_names(w.env.MacroForgeCore.ideas("script")), "Coords")
    end
end)

test("missing or damaged idea data gives an empty list, never an error", function()
    for _, bad in ipairs({ false, "text", 5, {}, { macros = "x", scripts = 3 },
                           { macros = { WARLOCK = "x" } }, { macros = { WARLOCK = { 1, "a", {} } } } }) do
        local w = ideas_world({}, bad)
        eq(#w.env.MacroForgeCore.ideas("macro"), 0)
        eq(#w.env.MacroForgeCore.ideas("script"), 0)
    end
end)

test("damaged entries are skipped and the good ones kept", function()
    local d = copy(IDEAS)
    table.insert(d.macros.WARLOCK, 2, { name = "No body", icon = 1, why = "x" })
    table.insert(d.macros.WARLOCK, { name = "No why", icon = 1, body = "/cast Fear" })
    table.insert(d.macros.WARLOCK, { name = "Bad level", icon = 1, why = "x", body = "/cast Fear", level = "8" })
    table.insert(d.macros.WARLOCK, { name = "Long", icon = 1, why = "x", body = string.rep("x", 256) })
    local w = ideas_world({}, d)
    eq(idea_names(w.env.MacroForgeCore.ideas("macro")), "Mouseover Fear,Curse Picker")
end)

test("Add creates the macro on this character with the idea's icon and text", function()
    local w = ideas_world()
    local idea = w.env.MacroForgeCore.ideas("macro")[1]
    eq(w.env.MacroForgeCore.ideaAdded(idea), false)
    eq(w.env.MacroForgeCore.addIdea(idea), true)
    eq(names(w.char), "Fear MO,Mouseover Fear")
    eq(w.char[2].icon, 136183)
    eq(w.char[2].body:gsub("%s+$", ""), idea.body)  -- the game stores a trailing newline
    eq(names(w.account), "Tap")
    truthy(chat_has(w, "Added 'Mouseover Fear' for this character"), chat_dump(w))
    eq(w.env.MacroForgeCore.ideaAdded(idea), true)
end)

test("adding the same idea twice does not duplicate or change anything", function()
    local w = ideas_world()
    local idea = w.env.MacroForgeCore.ideas("macro")[1]
    w.env.MacroForgeCore.addIdea(idea)
    w.chat = {}
    eq(w.env.MacroForgeCore.addIdea(idea), false)
    eq(names(w.char), "Fear MO,Mouseover Fear")
    truthy(chat_has(w, "you already have a macro named 'Mouseover Fear'"), chat_dump(w))
end)

test("a macro of the player's own with the same name is never overwritten", function()
    local w = ideas_world()
    w.add_macro("Mouseover Fear", 5, "/cast Shadow Bolt", true)
    local idea = w.env.MacroForgeCore.ideas("macro")[1]
    eq(w.env.MacroForgeCore.addIdea(idea), false)
    eq(w.char[2].body:gsub("%s+$", ""), "/cast Shadow Bolt")
    eq(w.char[2].icon, 5)
end)

test("a same-named macro in the other scope does not block Add", function()
    local w = ideas_world()
    w.add_macro("Mouseover Fear", 5, "/cast X", false)  -- account-wide
    local idea = w.env.MacroForgeCore.ideas("macro")[1]
    eq(w.env.MacroForgeCore.ideaAdded(idea), false)
    eq(w.env.MacroForgeCore.addIdea(idea), true)
end)

test("a script example is added account-wide and the Blizzard notice appears once", function()
    local w = ideas_world()
    local idea = w.env.MacroForgeCore.ideas("script")[1]
    eq(w.env.MacroForgeCore.addIdea(idea), true)
    eq(names(w.account), "Tap,Coords")
    eq(w.account[2].body:gsub("%s+$", ""), "/run print(GetZoneText())")
    truthy(chat_has(w, "Added 'Coords' for all your characters"), chat_dump(w))
    truthy(chat_has(w, "Blizzard shows a warning"), chat_dump(w))
    local d = copy(IDEAS)
    d.scripts[2] = { name = "Second", icon = 1, why = "x", script = true, body = "/run print(2)" }
    w.env.MacroForgeSuggestions = d
    w.chat = {}
    eq(w.env.MacroForgeCore.addIdea(w.env.MacroForgeCore.ideas("script")[2]), true)
    truthy(not chat_has(w, "Blizzard shows a warning"), "the notice must not repeat")
end)

test("a script idea with a syntax error or without /run is refused", function()
    for _, body in ipairs({ "/run print(", "/cast Fear", "print(1)" }) do
        local d = copy(IDEAS)
        d.scripts[1].body = body
        local w = ideas_world({}, d)
        eq(w.env.MacroForgeCore.addIdea(w.env.MacroForgeCore.ideas("script")[1]), false)
        eq(#w.account, 1)
        truthy(chat_has(w, "was not installed"), body .. " / " .. chat_dump(w))
    end
end)

test("Add is refused in combat, and a client error does not escape", function()
    local w = ideas_world({ combat = true })
    eq(w.env.MacroForgeCore.addIdea(w.env.MacroForgeCore.ideas("macro")[1]), false)
    truthy(chat_has(w, "in combat"), chat_dump(w))
    eq(#w.char, 0)  -- nothing was applied at login either (in combat)
    local blocked = ideas_world({ create_fails = true })
    local ok, result = pcall(blocked.env.MacroForgeCore.addIdea, blocked.env.MacroForgeCore.ideas("macro")[1])
    truthy(ok, tostring(result))
    eq(result, false)
    truthy(chat_has(blocked, "could not create"), chat_dump(blocked))
end)

test("Add refuses a damaged idea", function()
    local w = ideas_world()
    for _, bad in ipairs({ {}, { name = "x" }, { name = "x", icon = 1, body = "y" }, "text" }) do
        eq(w.env.MacroForgeCore.addIdea(bad), false)
    end
    eq(#w.char, 1)
    truthy(chat_has(w, "damaged"), chat_dump(w))
end)

test("a full macro list is reported, not crashed on", function()
    local w = ideas_world({ char_max = 1 })
    eq(w.env.MacroForgeCore.addIdea(w.env.MacroForgeCore.ideas("macro")[1]), false)
    eq(#w.char, 1)
end)

test("sending a request records it in the history, newest first", function()
    local w = ui_world()
    spy_strip(w)
    w.env.SlashCmdList.MACROFORGE("ask first request")
    w.env.SlashCmdList.MACROFORGE("script second request")
    local history = w.env.MacroForgeCore.history()
    eq(#history, 2)
    eq(history[1].text, "second request")
    eq(history[1].mode, "script")
    eq(history[2].text, "first request")
    eq(history[2].mode, "macro")
    eq(history[2].char, "Testy Mage")
    eq(history[2].reply, nil)
    truthy(history[1].time > 0)
end)

test("a reply is stored with the request it answers", function()
    local w = ui_world()
    spy_strip(w)
    w.env.SlashCmdList.MACROFORGE("ask make a fear macro")
    local id = w.env.MacroForgeRequests[1].id
    w.env.MacroForge_ShowReplies({ replies = { { id = 9, req = id + 7, text = "for another request" },
                                               { id = 10, req = id, text = "Done: Fear MO added." } } })
    local entry = w.env.MacroForgeCore.history()[1]
    eq(entry.reply, "Done: Fear MO added.")
    local pending = ui_world()
    spy_strip(pending)
    pending.env.SlashCmdList.MACROFORGE("ask x")
    pending.env.MacroForge_ShowReplies({ replies = { { id = 1, req = pending.env.MacroForgeRequests[1].id,
                                                       text = "working", pending = true } } })
    eq(pending.env.MacroForgeCore.history()[1].reply, nil)
end)

test("a reply goes to the request it answers and to no other", function()
    local w = ui_world()
    spy_strip(w)
    w.env.SlashCmdList.MACROFORGE("ask first")
    w.env.SlashCmdList.MACROFORGE("ask second")
    local first = w.env.MacroForgeRequests[1].id
    w.env.MacroForge_ShowReplies({ replies = { { id = 6, req = first, text = "answer to the first" } } })
    local history = w.env.MacroForgeCore.history()
    eq(history[1].text, "second")
    eq(history[1].reply, nil)
    eq(history[2].text, "first")
    eq(history[2].reply, "answer to the first")
end)

test("a long reply is cut and a repeated reply changes nothing", function()
    local w = ui_world()
    spy_strip(w)
    w.env.SlashCmdList.MACROFORGE("ask x")
    local id = w.env.MacroForgeRequests[1].id
    local calls = 0
    w.env.MacroForgeCore.onHistory = function() calls = calls + 1 end
    local data = { replies = { { id = 3, req = id, text = string.rep("r", 5000) } } }
    w.env.MacroForge_ShowReplies(data)
    eq(#w.env.MacroForgeCore.history()[1].reply, 600)
    eq(calls, 1)
    w.env.MacroForge_ShowReplies(data)
    eq(calls, 1)
end)

test("the history keeps the newest 30 requests", function()
    local w = ui_world()
    spy_strip(w)
    for i = 1, 35 do w.env.SlashCmdList.MACROFORGE("ask request " .. i) end
    local history = w.env.MacroForgeCore.history()
    eq(#history, 30)
    eq(history[1].text, "request 35")
    eq(history[30].text, "request 6")
end)

test("the history callback fires when a request is recorded", function()
    local w = ui_world()
    spy_strip(w)
    local calls = 0
    w.env.MacroForgeCore.onHistory = function() calls = calls + 1 end
    w.env.SlashCmdList.MACROFORGE("ask hello")
    eq(calls, 1)
    w.env.MacroForgeCore.onHistory = function() error("a broken listener") end
    w.env.SlashCmdList.MACROFORGE("ask again")  -- must not break sending
    eq(#w.env.MacroForgeCore.history(), 2)
end)

test("damaged saved history is skipped and a missing one is created", function()
    local w = world({}, data())
    w.env.MacroForgeHistory = { "x", 5, {}, { id = "a", text = "b" }, { id = 1, text = 7 },
                                { id = 2, text = "ok", time = "later", mode = "weird", char = 5, reply = 9 },
                                { id = 3, text = "kept", time = 50, mode = "script", char = "A", reply = "R" } }
    w.fire("PLAYER_LOGIN")
    local history = w.env.MacroForgeCore.history()
    eq(#history, 2)
    eq(history[1].text, "kept")
    eq(history[1].reply, "R")
    eq(history[2].time, 0)
    eq(history[2].mode, "macro")
    eq(history[2].char, "")
    eq(history[2].reply, nil)
    local fresh = world({}, data())
    fresh.env.MacroForgeHistory = "garbage"
    fresh.fire("PLAYER_LOGIN")
    eq(#fresh.env.MacroForgeCore.history(), 0)
end)

-- the side panel ------------------------------------------------------------------------------------------

local function panel_world(opts)
    local w = ui_world(opts)
    w.env.MacroForgeSuggestions = copy(IDEAS)
    click(w.env.MacroForgeMinimapButton)
    w.chat = {}
    return w
end

local function shown_cards(panel)
    local out = {}
    for _, card in ipairs(panel.cards) do if card.shown then out[#out + 1] = card end end
    return out
end

local function shown_rows(panel)
    local out = {}
    for _, row in ipairs(panel.rows) do if row.shown then out[#out + 1] = row end end
    return out
end

test("the window has Ideas and History buttons and no panel until one is clicked", function()
    local w = panel_world()
    truthy(w.find("Button", "Macro ideas"), "no ideas button")
    truthy(w.find("Button", "History"), "no history button")
    eq(w.env.MacroForgeSidePanel, nil)
end)

test("the ideas button follows the Macro / Script mode", function()
    local w = panel_world()
    click(w.find("Button", "Script"))
    truthy(w.find("Button", "Script examples"), "the button should now say Script examples")
    click(w.find("Button", "Macro"))
    truthy(w.find("Button", "Macro ideas"))
end)

test("Ideas opens a panel with a card per idea for the class", function()
    local w = panel_world()
    click(w.find("Button", "Macro ideas"))
    local panel = w.env.MacroForgeSidePanel
    truthy(panel.shown, "panel should be open")
    eq(panel.view, "ideas")
    eq(panel.TitleText:GetText(), "Macro ideas")
    local cards = shown_cards(panel)
    eq(#cards, 2)
    eq(cards[1].name:GetText(), "Mouseover Fear")
    eq(cards[1].why:GetText(), "Fears the unit under your mouse.")
    eq(cards[1].body:GetText(), "#showtooltip\n/cast [@mouseover,harm,nodead][] Fear")
    eq(cards[1].icon.props.Texture[1], 136183)
    eq(cards[1].note:GetText(), "Needs level 8")
    eq(cards[1].add:GetText(), "Add")
    eq(cards[2].name:GetText(), "Curse Picker")
end)

test("a card marks a level the character has not reached", function()
    local w = panel_world({ level = 10 })
    click(w.find("Button", "Macro ideas"))
    local cards = shown_cards(w.env.MacroForgeSidePanel)
    eq(cards[1].note:GetText(), "Needs level 8")
    eq(cards[2].note:GetText(), "Needs level 20")
    eq(cards[2].note.props.TextColor[2], 0.4)   -- shown in red: not reached yet
    eq(cards[1].note.props.TextColor[2], 0.6)   -- grey: fine
end)

test("cards are stacked downwards without overlapping", function()
    local w = panel_world()
    click(w.find("Button", "Macro ideas"))
    local cards = shown_cards(w.env.MacroForgeSidePanel)
    local first, second = cards[1].props.Point, cards[2].props.Point
    eq(first[5], 0)
    truthy(second[5] < first[5], "the second card must be lower")
    eq(second[5], -(cards[1].props.Height[1] + 8))
end)

test("clicking the same button again closes the panel; the other button switches the view", function()
    local w = panel_world()
    click(w.find("Button", "Macro ideas"))
    click(w.find("Button", "Macro ideas"))
    truthy(not w.env.MacroForgeSidePanel.shown, "second click should close it")
    click(w.find("Button", "Macro ideas"))
    click(w.find("Button", "History"))
    truthy(w.env.MacroForgeSidePanel.shown)
    eq(w.env.MacroForgeSidePanel.view, "history")
    eq(w.env.MacroForgeSidePanel.TitleText:GetText(), "History")
    click(w.find("Button", "History"))
    truthy(not w.env.MacroForgeSidePanel.shown)
end)

test("Add creates the macro, the button turns to Added, and it stays that way when redrawn", function()
    local w = panel_world()
    click(w.find("Button", "Macro ideas"))
    local panel = w.env.MacroForgeSidePanel
    click(shown_cards(panel)[1].add)
    eq(names(w.char), "Fear MO,Mouseover Fear")
    eq(shown_cards(panel)[1].add:GetText(), "Added")
    eq(shown_cards(panel)[1].add.props.Disabled, true)
    eq(shown_cards(panel)[2].add:GetText(), "Add")
    click(w.find("Button", "History"))
    click(w.find("Button", "Macro ideas"))
    eq(shown_cards(panel)[1].add:GetText(), "Added")
    truthy(chat_has(w, "Added 'Mouseover Fear' for this character"), chat_dump(w))
    truthy(window_of(w).status:GetText():find("Added 'Mouseover Fear'", 1, true), window_of(w).status:GetText())
end)

test("switching to Script mode redraws an open panel with script examples", function()
    local w = panel_world()
    click(w.find("Button", "Macro ideas"))
    click(w.find("Button", "Script"))
    local panel = w.env.MacroForgeSidePanel
    truthy(panel.shown)
    eq(panel.TitleText:GetText(), "Script examples")
    local cards = shown_cards(panel)
    eq(#cards, 1)
    eq(cards[1].name:GetText(), "Coords")
    eq(cards[1].note:GetText(), "Any character")
    click(cards[1].add)
    eq(names(w.account), "Tap,Coords")
    eq(cards[1].add:GetText(), "Added")
end)

test("a class with no ideas gets a friendly message, not an empty panel", function()
    local w = panel_world({ class = "ROGUE" })
    click(w.find("Button", "Macro ideas"))
    local panel = w.env.MacroForgeSidePanel
    eq(#shown_cards(panel), 0)
    truthy(panel.empty.shown)
    truthy(panel.empty:GetText():find("no ideas for your class", 1, true))
end)

test("text in an idea cannot show colors or links", function()
    local w = panel_world()
    local d = copy(IDEAS)
    d.macros.WARLOCK[1].why = "|cffff0000RED|r"
    w.env.MacroForgeSuggestions = d
    click(w.find("Button", "Macro ideas"))
    eq(shown_cards(w.env.MacroForgeSidePanel)[1].why:GetText(), "||cffff0000RED||r")
end)

test("the panel opens beside the window, on the left when the right has no room", function()
    local w = panel_world()
    click(w.find("Button", "Macro ideas"))
    eq(w.env.MacroForgeSidePanel.props.Point[1], "TOPLEFT")
    click(w.find("Button", "Macro ideas"))
    w.env.UIParent:SetSize(1920, 1080)
    window_of(w).props.Right = { 1800 }
    click(w.find("Button", "Macro ideas"))
    eq(w.env.MacroForgeSidePanel.props.Point[1], "TOPRIGHT")
    window_of(w).props.Right = { 700 }
    click(w.find("Button", "Macro ideas"))
    click(w.find("Button", "Macro ideas"))
    eq(w.env.MacroForgeSidePanel.props.Point[1], "TOPLEFT")
end)

test("Esc closes the panel, and closing the window closes it too", function()
    local w = panel_world()
    click(w.find("Button", "Macro ideas"))
    local found = false
    for _, name in ipairs(w.env.UISpecialFrames) do if name == "MacroForgeSidePanel" then found = true end end
    truthy(found, "the panel is not registered for Esc")
    window_of(w):Hide()
    window_of(w).scripts.OnHide(window_of(w))
    truthy(not w.env.MacroForgeSidePanel.shown)
end)

test("History lists the requests, newest first, with their replies", function()
    local w = panel_world()
    spy_strip(w)
    w.env.SlashCmdList.MACROFORGE("ask first request")
    w.env.SlashCmdList.MACROFORGE("script second request")
    click(w.find("Button", "History"))
    local panel = w.env.MacroForgeSidePanel
    local rows = shown_rows(panel)
    eq(#rows, 2)
    eq(rows[1].text:GetText(), "second request")
    eq(rows[1].head:GetText(), "Script, just now")
    eq(rows[2].text:GetText(), "first request")
    eq(rows[2].head:GetText(), "Macro, just now")
    eq(rows[2].reply:GetText(), "No reply yet.")
end)

test("an empty history says how to get started", function()
    local w = panel_world()
    click(w.find("Button", "History"))
    local panel = w.env.MacroForgeSidePanel
    eq(#shown_rows(panel), 0)
    truthy(panel.empty:GetText():find("No requests yet", 1, true))
end)

test("a reply that arrives while History is open appears at once", function()
    local w = panel_world()
    spy_strip(w)
    w.env.SlashCmdList.MACROFORGE("ask make a fear macro")
    click(w.find("Button", "History"))
    eq(shown_rows(w.env.MacroForgeSidePanel)[1].reply:GetText(), "No reply yet.")
    local id = w.env.MacroForgeRequests[1].id
    w.env.MacroForge_ShowReplies({ replies = { { id = 4, req = id, text = "Added Fear MO." } } })
    eq(shown_rows(w.env.MacroForgeSidePanel)[1].reply:GetText(), "Added Fear MO.")
end)

test("a long reply is shortened until the row is clicked", function()
    local w = panel_world()
    spy_strip(w)
    w.env.SlashCmdList.MACROFORGE("ask x")
    local long = string.rep("word ", 60)
    w.env.MacroForge_ShowReplies({ replies = { { id = 4, req = w.env.MacroForgeRequests[1].id, text = long } } })
    click(w.find("Button", "History"))
    local row = shown_rows(w.env.MacroForgeSidePanel)[1]
    eq(#row.reply:GetText(), 140)
    truthy(row.reply:GetText():sub(-3) == "...")
    row.scripts.OnMouseUp(row)
    eq(row.reply:GetText(), long)
    row.scripts.OnMouseUp(row)
    eq(#row.reply:GetText(), 140)
end)

test("Use again puts the request back in the box and picks its mode", function()
    local w = panel_world()
    spy_strip(w)
    w.env.SlashCmdList.MACROFORGE("script print my coordinates")
    click(w.find("Button", "History"))
    local row = shown_rows(w.env.MacroForgeSidePanel)[1]
    click(row.again)
    eq(window_of(w).mode, "script")
    eq(window_of(w).edit:GetText(), "print my coordinates")
    truthy(window_of(w).status:GetText():find("Request loaded", 1, true), window_of(w).status:GetText())
end)

test("history text cannot show colors or links either", function()
    local w = panel_world()
    spy_strip(w)
    w.env.SlashCmdList.MACROFORGE("ask |cffff0000RED|r")
    click(w.find("Button", "History"))
    eq(shown_rows(w.env.MacroForgeSidePanel)[1].text:GetText(), "||cffff0000RED||r")
end)

test("the window keeps its layout: the new buttons sit above Apply and nothing overlaps", function()
    local w = panel_world()
    local ideas, history = w.find("Button", "Macro ideas"), w.find("Button", "History")
    local apply, advanced = w.find("Button", "Apply new macros"), w.find("Button", "Advanced  >>")
    eq(ideas.props.Point[5], history.props.Point[5])
    truthy(apply.props.Point[5] < ideas.props.Point[5] - 24, "Apply must be below the new row")
    truthy(advanced.props.Point[5] < apply.props.Point[5] - 24)
    truthy(window_of(w).props.Size[2] >= 412)
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
