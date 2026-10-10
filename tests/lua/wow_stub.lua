-- A small fake of the parts of the WoW client API that the MacroForge addon uses.
-- It is only as good as these assumptions about the real client (documented in docs/TESTING.md):
--   * account macros are indices 1..120, character macros 121..138, stored without gaps
--   * the game stores every macro body with a trailing newline
--   * unknown conditionals evaluate to true in SecureCmdOptionParse
-- Each world has its own global table, so tests cannot affect each other.

local M = {}

local function join(...)
    local parts = {}
    for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
    return table.concat(parts, " ")
end

function M.new_world(opts)
    opts = opts or {}
    local w = {
        chat = {},          -- every line the addon printed
        account = {},       -- account macros: { name, icon, body }
        char = {},          -- character macros
        addon_files = {},   -- addon name -> Lua source (what LoadAddOn runs)
        loaded = {},        -- addon name -> true once loaded
        reloads = 0,        -- how many times ReloadUI was called
        now = opts.now or 1790000000,
        frames = {},
        timers = {},        -- pending C_Timer.After callbacks: { at, fn }
        clock = 0,          -- seconds advanced with w.advance
        cvars = { screenshotFormat = "jpeg" },
        shots = 0,          -- how many times Screenshot() was called
        formats_at_shot = {},  -- screenshotFormat at each Screenshot() call
        sounds = {},        -- sound ids passed to PlaySound
    }
    local ACCOUNT_MAX, CHAR_MAX = opts.account_max or 120, opts.char_max or 18
    local env = setmetatable({}, { __index = _G })  -- built-ins (pairs, string...) come from _G
    env._G = env
    w.env = env

    -- chat and slash commands (the real client provides SlashCmdList) --------------------------
    env.print = function(...) w.chat[#w.chat + 1] = join(...) end
    env.SlashCmdList = {}
    env.SLASH_FAKEADDON1 = "/fakeaddon"  -- another addon's command, so the probe has something to list

    -- macros --------------------------------------------------------------------------------
    local function locate(i)
        if type(i) ~= "number" or i ~= math.floor(i) then return nil end
        if i >= 1 and i <= ACCOUNT_MAX then return w.account, i, false end
        if i > ACCOUNT_MAX and i <= ACCOUNT_MAX + CHAR_MAX then return w.char, i - ACCOUNT_MAX, true end
        return nil
    end

    function w.add_macro(name, icon, body, per_char)
        local list = per_char and w.char or w.account
        list[#list + 1] = { name = name, icon = icon, body = body .. "\n" }
    end

    env.GetNumMacros = function() return #w.account, #w.char end
    -- The real Forever client returns only name, icon and body (confirmed by a real export); the
    -- documented 4th "isLocal" value is absent, so the addon must not rely on it. Tests can turn it on
    -- with opts.report_islocal to cover clients that do return it.
    env.GetMacroInfo = function(i)
        local list, idx, is_local = locate(i)
        local m = list and list[idx]
        if not m then return nil end
        if opts.report_islocal then return m.name, m.icon, m.body, is_local end
        return m.name, m.icon, m.body
    end
    env.CreateMacro = function(name, icon, body, per_char)
        if type(name) ~= "string" or type(body) ~= "string" then error("bad argument to CreateMacro") end
        if opts.create_fails then error("CreateMacro is blocked") end
        local list = per_char and w.char or w.account
        if #list >= (per_char and CHAR_MAX or ACCOUNT_MAX) then error("macro slots are full") end
        list[#list + 1] = { name = name, icon = icon, body = body .. "\n" }
        return (per_char and ACCOUNT_MAX or 0) + #list
    end
    env.EditMacro = function(i, name, icon, body)
        if opts.edit_fails then error("EditMacro is blocked") end
        local list, idx = locate(i)
        local m = list and list[idx]
        if not m then error("no such macro " .. tostring(i)) end
        list[idx] = { name = name or m.name, icon = icon or m.icon, body = body and (body .. "\n") or m.body }
        return i
    end
    env.DeleteMacro = function(i)
        local list, idx = locate(i)
        if not (list and list[idx]) then error("no such macro " .. tostring(i)) end
        table.remove(list, idx)
    end

    -- the player and the pet ----------------------------------------------------------------
    local surname = opts.surname
    if surname == nil then surname = "Mage" end
    env.UnitName = function(unit)
        if unit == "pet" then return opts.pet and opts.pet.name or nil end
        if unit == "target" then return opts.target end
        return opts.first_name or "Testy", surname ~= "" and surname or nil
    end
    -- opts.identity_only_unitname: only UnitName exposes the surname (as on the Forever client)
    env.UnitFullName = function()
        if opts.identity_only_unitname then return opts.first_name or "Testy" end
        return opts.first_name or "Testy", surname ~= "" and surname or nil
    end
    env.GetUnitName = function()
        local first = opts.first_name or "Testy"
        if opts.identity_only_unitname then return first end
        return surname ~= "" and (first .. " " .. surname) or first
    end
    env.UnitClass = function() return "Warlock", "WARLOCK" end
    env.UnitLevel = function() return opts.level or 30 end
    env.UnitExists = function(unit) return unit == "pet" and opts.pet ~= nil end
    env.UnitCreatureFamily = function() return opts.pet and opts.pet.family or nil end
    env.UnitCreatureType = function() return opts.pet and "Demon" or nil end
    env.GetPetActionInfo = function(i) return opts.pet and opts.pet.actions and opts.pet.actions[i] or nil end
    env.InCombatLockdown = function() return opts.combat == true end

    -- spellbook (modern namespaced API, as on Forever) -----------------------------------------------
    local spells = opts.spells or {}
    env.Enum = { SpellBookSpellBank = { Player = 0 } }
    env.C_SpellBook = {
        GetNumSpellBookSkillLines = function() return #spells > 0 and 1 or 0 end,
        GetSpellBookSkillLineInfo = function() return { name = "General", itemIndexOffset = 0, numSpellBookItems = #spells } end,
        GetSpellBookItemInfo = function(i) return spells[i] end,
    }

    -- misc client functions --------------------------------------------------------------------
    env.time = function() return w.now end
    env.date = function() return "2026-10-09 12:00:00" end
    env.strtrim = function(s) return (tostring(s):gsub("^%s+", ""):gsub("%s+$", "")) end
    env.GetBuildInfo = function() return "1.60.1", "70291", "Oct 8 2026", 16001, "", "" end
    env.ReloadUI = function()
        if opts.reload_fails then error("ReloadUI is protected") end
        w.reloads = w.reloads + 1
    end

    -- timers, screenshots, sound and cvars -----------------------------------------------------
    env.C_Timer = { After = function(seconds, fn)
        w.timers[#w.timers + 1] = { at = w.clock + seconds, fn = fn }
    end }
    -- Run every timer that is due within `seconds` of fake time (timers may schedule more timers).
    function w.advance(seconds)
        local target = w.clock + seconds
        while true do
            local best
            for i, t in ipairs(w.timers) do
                if t.at <= target and (not best or t.at < w.timers[best].at) then best = i end
            end
            if not best then break end
            local t = table.remove(w.timers, best)
            if t.at > w.clock then w.clock = t.at end
            t.fn()
        end
        w.clock = target
    end
    env.GetCVar = function(name) return w.cvars[name] end
    env.SetCVar = function(name, value) w.cvars[name] = value; return true end
    env.Screenshot = function()
        if opts.screenshot_fails then error("Screenshot is not available") end
        w.shots = w.shots + 1
        w.formats_at_shot[#w.formats_at_shot + 1] = w.cvars.screenshotFormat
    end
    env.GetPhysicalScreenSize = function() return 3840, 2160 end
    env.PlaySound = function(id) w.sounds[#w.sounds + 1] = id end

    -- widgets: frames, buttons, textures, font strings -------------------------------------------------
    -- A generic fake: Set<X>/Get<X> store and return values, Show/Hide track visibility, SetScript
    -- stores handlers (tests call them), Create* makes child widgets. w.widgets lists everything made.
    w.widgets = {}
    w.cursor = { x = 0, y = 0 }
    w.tooltip = {}
    local widget
    local methods = {}
    function methods.SetScript(self, name, fn) self.scripts[name] = fn end
    function methods.GetScript(self, name) return self.scripts[name] end
    function methods.HookScript(self, name, fn) self.scripts[name] = fn end
    function methods.RegisterEvent(self, name) self.events[name] = true end
    function methods.Show(self) self.shown = true end
    function methods.Hide(self) self.shown = false end
    function methods.SetShown(self, flag) self.shown = flag and true or false end
    function methods.IsShown(self) return self.shown end
    function methods.IsVisible(self) return self.shown end
    function methods.GetParent(self) return self.parent end
    function methods.SetText(self, text) self.props.Text = tostring(text) end
    function methods.GetText(self) return self.props.Text or "" end
    function methods.SetChecked(self, flag) self.props.Checked = flag and true or false end
    function methods.GetChecked(self) return self.props.Checked == true end
    function methods.GetTexture(self) return self.props.Texture end
    function methods.GetCenter() return 500, 500 end
    function methods.GetWidth(self) return (self.props.Size and self.props.Size[1]) or 140 end
    function methods.GetEffectiveScale() return 1 end
    function methods.CreateTexture(self) return widget("Texture", nil, self) end
    function methods.CreateFontString(self) return widget("FontString", nil, self) end
    function methods.AddLine(self, ...) w.tooltip[#w.tooltip + 1] = (...) end
    function methods.SetOwner(self) w.tooltip = {} end
    function methods.GetFrameLevel() return 1 end
    function methods.GetStringHeight() return 12 end

    widget = function(kind, name, parent, template)
        local obj = { kind = kind, name = name, parent = parent, template = template, props = {},
                      scripts = {}, events = {}, shown = true }
        setmetatable(obj, { __index = function(_, method)
            if methods[method] then return methods[method] end
            local prop = method:match("^Set(%u.*)$")
            if prop then return function(self, ...) self.props[prop] = { ... } end end
            prop = method:match("^Get(%u.*)$")
            if prop then return function(self) return unpack(self.props[prop] or {}) end end
            return function() end  -- any other call (ClearAllPoints, EnableMouse, ...) does nothing
        end })
        if template and template:find("BasicFrame") then obj.TitleText = widget("FontString", nil, obj) end
        if template and template:find("CheckButton") then obj.Text = widget("FontString", nil, obj) end
        w.widgets[#w.widgets + 1] = obj
        return obj
    end

    function w.find(kind, text)  -- the widget of this kind whose text is `text`
        for _, obj in ipairs(w.widgets) do
            if obj.kind == kind and obj.props.Text == text then return obj end
        end
    end

    env.UIParent = widget("Frame", "UIParent")
    env.Minimap = widget("Frame", "Minimap")
    env.GameTooltip = widget("Frame", "GameTooltip")
    env.UISpecialFrames = {}
    env.tinsert = table.insert
    env.GetCursorPosition = function() return w.cursor.x, w.cursor.y end
    env.CreateFrame = function(kind, name, parent, template)
        if template and opts.fail_templates then error("unknown template " .. template) end
        local frame = widget(kind, name, parent, template)
        w.frames[#w.frames + 1] = frame
        if name then env[name] = frame end
        return frame
    end
    function w.fire(event)
        for _, frame in ipairs(w.frames) do
            if frame.events[event] and frame.scripts.OnEvent then frame.scripts.OnEvent(frame, event) end
        end
    end

    -- load-on-demand addons (the slots) -----------------------------------------------------------
    local function load_addon(name)
        local source = w.addon_files[name]
        if not source then error("Interface\\AddOns\\" .. tostring(name) .. " does not exist", 2) end
        if w.loaded[name] then return false, "ALREADY_LOADED" end
        w.loaded[name] = true
        local fn, err = loadstring(source, "=" .. name)
        if not fn then error(err, 2) end
        setfenv(fn, env)
        fn()
        return true
    end
    env.C_AddOns = {
        LoadAddOn = load_addon,
        DoesAddOnExist = function(name) return w.addon_files[name] ~= nil end,
        IsAddOnLoaded = function(name) return w.loaded[name] == true end,
    }

    -- the macro-option parser: unknown conditionals are true, like the real client --------------------
    local known = {}
    for _, name in ipairs(opts.known_conditionals or { "harm", "help", "dead", "combat", "pet", "mod", "exists" }) do
        known[name] = true
    end
    env.SecureCmdOptionParse = function(options)
        local group, action = options:match("^%[([^%]]*)%]%s*(.*)$")
        if not group then return options end
        local yes, no = action:match("^(%S+);%s*(%S+)$")
        local result = true
        for token in group:gmatch("[^,]+") do
            local negate = false
            local name = token:match("^no(.+)$")
            if name and known[name:match("^[^:]+")] then negate, token = true, name end
            local base = token:match("^[^:]+")
            if known[base] then
                local value = (opts.conditions or {})[token] == true
                if negate then value = not value end
                if not value then result = false end
            end  -- unknown: ignored, so the group stays true
        end
        return result and yes or no
    end

    return w
end

return M
