-- MacroForge probe (/mf probe): read-only capability tests. Nothing here casts,
-- targets or automates anything; it only asks the client what it supports, using
-- public functions, and saves the answers to SavedVariables (MacroForgeProbe).
--   * slash commands: every SLASH_* global the client defines
--   * conditionals:   run through SecureCmdOptionParse, the parser macros use
--   * API presence:   does this global function / namespace exist on this build
-- macroforge.py then turns the result into reference/compat-<toc>.md.

local CONDITIONALS = {
    "help", "harm", "dead", "exists", "party", "raid", "unithasvehicleui",
    "combat", "stealth", "mounted", "flying", "swimming", "indoors", "outdoors", "resting",
    "channeling", "group", "group:party", "group:raid", "pet", "flyable", "advflyable",
    "petbattle", "pvpcombat", "vehicleui", "canexitvehicle", "possessbar", "overridebar",
    "extrabar", "bonusbar", "cursor", "shapeshift", "modifier",
    "mod:shift", "mod:ctrl", "mod:alt", "btn:1", "button:2",
    "form:1", "stance:1", "spec:1", "talent:1/1", "known:Attack", "equipped:Shields", "worn:Shields",
    "actionbar:1", "bar:1", "house:inside",
}

local API = {
    "CreateMacro", "EditMacro", "DeleteMacro", "GetMacroInfo", "GetNumMacros", "SecureCmdOptionParse",
    "GetBuildInfo", "GetSpellInfo", "IsSpellKnown", "IsPlayerSpell", "GetSpellTexture",
    "GetNumSpellTabs", "GetSpellTabInfo", "GetSpellBookItemName", "UnitFullName", "GetUnitName",
    "C_Spell", "C_SpellBook", "C_AddOns", "C_Timer", "C_Item", "C_Macro",
    "SecureCmdList", "hash_SlashCmdList", "MAX_ACCOUNT_MACROS", "MAX_CHARACTER_MACROS",
}

local function parse(opts)
    local ok, action, target = pcall(SecureCmdOptionParse, opts)
    if not ok then return "ERROR" end
    return tostring(action) .. (target and ("@" .. tostring(target)) or "")
end

local function runProbe()
    local out = { time = date("%Y-%m-%d %H:%M:%S"), conditionals = {}, api = {}, slash = {} }
    local version, build, bdate, toc, localized, btype = GetBuildInfo()
    out.build = { version = version, build = build, date = bdate, toc = toc, localized = localized, buildType = btype }

    local seen = {}
    for k, v in pairs(_G) do
        if type(k) == "string" and type(v) == "string" and k:match("^SLASH_") and v:sub(1, 1) == "/" then
            seen[v:lower()] = true
        end
    end
    for cmd in pairs(seen) do out.slash[#out.slash + 1] = cmd end
    table.sort(out.slash)
    if SecureCmdList then  -- the secure (macro-only) command handlers, by internal name
        out.secure = {}
        for name in pairs(SecureCmdList) do out.secure[#out.secure + 1] = name end
        table.sort(out.secure)
    end

    -- Each token is tested positively and negated, against a made-up control token.
    -- A real conditional gives opposite answers for the pair; an unknown one is
    -- compared against the control to tell "false right now" from "not supported".
    -- `both` = [X,noX]: contradictory for a real conditional (always N), but an unknown
    -- conditional is ignored by the parser (treated as true), so it stays Y. This
    -- separates "supported" from "unknown" regardless of current game state.
    local function test(token)
        return { pos = parse("[" .. token .. "] Y; N"), neg = parse("[no" .. token .. "] Y; N"),
                 both = parse("[" .. token .. ",no" .. token .. "] Y; N") }
    end
    out.control = test("zzbogus")
    for _, token in ipairs(CONDITIONALS) do out.conditionals[token] = test(token) end
    out.targets = {}
    for _, unit in ipairs({ "player", "target", "focus", "mouseover", "pet", "cursor", "none", "party1", "targettarget" }) do
        out.targets[unit] = parse("[@" .. unit .. "] Y")
    end

    for _, name in ipairs(API) do out.api[name] = type(_G[name]) end
    return out
end

function MacroForge_Probe()
    MacroForgeProbe = runProbe()
    print(("|cff66ccffMacroForge:|r probe done (build %s, toc %s): %d slash commands, %d conditionals. /reload to write to disk, then run `python macroforge.py report`."):format(
        tostring(MacroForgeProbe.build.version), tostring(MacroForgeProbe.build.toc),
        #MacroForgeProbe.slash, #CONDITIONALS))
end
