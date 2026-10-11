-- The MacroForge minimap button and window: a front end for the same actions as the /mf commands.
-- It only calls MacroForgeCore (MacroForge.lua); it never touches macros itself.
local core = MacroForgeCore

local ICON = "Interface\\Icons\\INV_Misc_Note_06"
local FALLBACK_ICON = 134400  -- the question mark, present in every client
local WIDTH, HEIGHT_COLLAPSED, HEIGHT_EXPANDED = 380, 380, 600
local LEFT = 14

-- Saved between sessions (SavedVariables): where the button sits and whether it is hidden.
local function settings()
    if type(MacroForgeUI) ~= "table" then MacroForgeUI = {} end
    return MacroForgeUI
end

-- A child object that a template provides (a title, a checkbox label), or nil if it has none.
local function part(frame, ...)
    for _, key in ipairs({ ... }) do
        local value = frame[key]
        if value ~= nil and type(value) ~= "function" then return value end
    end
end

-- Creates a frame with a Blizzard template; if this client lacks it, falls back to a plain frame.
local function newFrame(kind, name, parent, template)
    if template then
        local ok, frame = pcall(CreateFrame, kind, name, parent, template)
        if ok and frame then return frame, true end
    end
    return CreateFrame(kind, name, parent), false
end

-- Window ------------------------------------------------------------------------------------------

local window
local lastStatus = "Ready."
local MODE = {
    macro = { heading = "Ask for a macro", idle = "Ready.",
              desc = "Describe what you want in plain English. Your PC builds it and the macro appears "
                     .. "here automatically, usually within a minute." },
    script = { heading = "Ask for a script",
               desc = "Describe a short script, such as \"print my coordinates\". Your PC writes it, "
                      .. "checks it and adds it as a macro, usually within a minute." },
}

local function setStatus(msg)
    lastStatus = tostring(msg)
    if window then window.status:SetText(lastStatus) end
end
core.onStatus = setStatus

local function label(parent, text, font, width, x, y, height)
    local fs = parent:CreateFontString(nil, "ARTWORK", font)
    fs:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
    fs:SetWidth(width)
    if height then fs:SetHeight(height) end
    fs:SetJustifyH("LEFT")
    fs:SetJustifyV("TOP")
    fs:SetText(text)
    return fs
end

local function button(parent, text, width, x, y, onClick)
    local b = newFrame("Button", nil, parent, "UIPanelButtonTemplate")
    b:SetSize(width, 24)
    b:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
    b:SetText(text)
    b:SetScript("OnClick", onClick)
    return b
end

local function divider(parent, y)
    local line = parent:CreateTexture(nil, "ARTWORK")
    line:SetPoint("TOPLEFT", parent, "TOPLEFT", LEFT, y)
    line:SetSize(WIDTH - 2 * LEFT, 1)
    line:SetColorTexture(1, 1, 1, 0.15)
    return line
end

local minimapButton  -- created at login; the window's checkbox controls it

local function applyButtonVisibility()
    if not minimapButton then return end
    if settings().hidden then minimapButton:Hide() else minimapButton:Show() end
    if window and window.showButton then window.showButton:SetChecked(not settings().hidden) end
end

local function toggleButton()
    settings().hidden = not settings().hidden or nil
    applyButtonVisibility()
    if settings().hidden then
        print("|cff66ccffMacroForge:|r minimap button hidden. Type /mf button to show it again.")
    end
end
core.toggleButton = toggleButton

local function setExpanded(expanded)
    window.advanced:SetShown(expanded)
    window:SetHeight(expanded and HEIGHT_EXPANDED or HEIGHT_COLLAPSED)
    window.toggle:SetText(expanded and "Advanced  <<" or "Advanced  >>")
    window.expanded = expanded
end

-- Switch between asking for a macro and asking for a script (remembered between sessions).
local function setMode(mode)
    mode = (mode == "script") and "script" or "macro"
    window.mode = mode
    settings().mode = mode
    window.heading:SetText(MODE[mode].heading)
    window.desc:SetText(MODE[mode].desc)
    for name, button in pairs(window.modeButtons) do
        if name == mode then button:Disable() else button:Enable() end  -- the active one looks pressed
    end
    MODE.script.idle = core.scriptNote or ""
    if lastStatus == MODE.macro.idle or lastStatus == MODE.script.idle then setStatus(MODE[mode].idle) end
end

local function send()
    local text = strtrim(window.edit:GetText())
    if text == "" then
        setStatus("Type what you want first, then press Send.")
        return
    end
    text = text:gsub("%s+", " ")
    if core.ask(text, window.mode) then
        window.edit:SetText("")
        window.edit:ClearFocus()
    end
end

local function addAdvanced(parent)
    local rows = {
        { "Export spellbook", core.export,
          "Saves your spells and pet so the PC can check spell names. Reload afterwards to save." },
        { "Probe this client", core.probe,
          "Tests which macro features this game version supports. Mostly for troubleshooting." },
        { "Reload UI", function() pcall(ReloadUI) end, "Writes exported data to disk (needed after Export or Probe)." },
    }
    local y = 0
    for _, row in ipairs(rows) do
        local action = row[2]
        button(parent, row[1], 150, LEFT, y, function() action() end)
        label(parent, row[3], "GameFontDisableSmall", 190, LEFT + 160, y + 2, 36)
        y = y - 40
    end
    local box = newFrame("CheckButton", nil, parent, "UICheckButtonTemplate")
    box:SetPoint("TOPLEFT", parent, "TOPLEFT", LEFT - 4, y)
    box:SetSize(26, 26)
    local text = part(box, "Text", "text")
    if text then text:SetText("Show minimap button")
    else label(parent, "Show minimap button", "GameFontNormalSmall", 200, LEFT + 26, y - 6) end
    box:SetChecked(not settings().hidden)
    box:SetScript("OnClick", function() toggleButton() end)
    parent:GetParent().showButton = box
    label(parent, "Slash commands still work: /mf ask <text>, /mf live, /mf ui", "GameFontDisableSmall",
          WIDTH - 2 * LEFT, LEFT, y - 40, 30)
end

local function buildWindow()
    local frame, templated = newFrame("Frame", "MacroForgeWindow", UIParent, "BasicFrameTemplateWithInset")
    window = frame
    frame:SetSize(WIDTH, HEIGHT_COLLAPSED)
    frame:SetPoint("CENTER", UIParent, "CENTER", 0, 40)
    frame:SetFrameStrata("DIALOG")
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:SetClampedToScreen(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", function(self) self:StartMoving() end)
    frame:SetScript("OnDragStop", function(self) self:StopMovingOrSizing() end)
    if not templated then
        local background = frame:CreateTexture(nil, "BACKGROUND")
        background:SetAllPoints()
        background:SetColorTexture(0, 0, 0, 0.85)
    end
    local title = part(frame, "TitleText")
    if title then title:SetText("MacroForge")
    else label(frame, "MacroForge", "GameFontNormalLarge", 200, LEFT, -8) end
    if UISpecialFrames then table.insert(UISpecialFrames, "MacroForgeWindow") end  -- Esc closes it

    -- Ask for a macro
    frame.heading = label(frame, MODE.macro.heading, "GameFontNormalLarge", 190, LEFT, -34)
    frame.desc = label(frame, MODE.macro.desc, "GameFontHighlightSmall", WIDTH - 2 * LEFT, LEFT, -58, 34)
    frame.modeButtons = {}
    for index, mode in ipairs({ "macro", "script" }) do
        local b = newFrame("Button", nil, frame, "UIPanelButtonTemplate")
        b:SetSize(72, 22)
        b:SetPoint("TOPLEFT", frame, "TOPLEFT", WIDTH - LEFT - 150 + (index - 1) * 76, -32)
        b:SetText(mode == "macro" and "Macro" or "Script")
        b:SetScript("OnClick", function() setMode(mode) end)
        frame.modeButtons[mode] = b
    end

    local scroll = newFrame("ScrollFrame", "MacroForgeRequestScroll", frame, "UIPanelScrollFrameTemplate")
    scroll:SetPoint("TOPLEFT", frame, "TOPLEFT", LEFT, -100)
    scroll:SetSize(WIDTH - 2 * LEFT - 28, 84)
    local shade = scroll:CreateTexture(nil, "BACKGROUND")
    shade:SetPoint("TOPLEFT", scroll, "TOPLEFT", -4, 4)
    shade:SetPoint("BOTTOMRIGHT", scroll, "BOTTOMRIGHT", 26, -4)
    shade:SetColorTexture(0, 0, 0, 0.5)
    local edit = CreateFrame("EditBox", nil, scroll)
    edit:SetMultiLine(true)
    edit:SetFontObject("ChatFontNormal")
    edit:SetAutoFocus(false)
    edit:SetMaxLetters(500)
    edit:SetSize(WIDTH - 2 * LEFT - 34, 84)
    scroll:SetScrollChild(edit)
    frame.edit = edit
    local counter = label(frame, "0/500", "GameFontDisableSmall", 80, LEFT, -192)
    edit:SetScript("OnTextChanged", function(self)
        counter:SetText(("%d/500"):format(#self:GetText()))
    end)
    edit:SetScript("OnEscapePressed", function(self) self:ClearFocus() end)
    scroll:EnableMouse(true)
    scroll:SetScript("OnMouseDown", function() edit:SetFocus() end)
    button(frame, "Send request", 130, WIDTH - LEFT - 130, -188, send)
    frame.status = label(frame, lastStatus, "GameFontNormalSmall", WIDTH - 2 * LEFT, LEFT, -220, 30)

    divider(frame, -256)
    button(frame, "Apply new macros", 150, LEFT, -268, function()
        setStatus("Looking for new macros...")
        core.live()
    end)
    label(frame, "Loads macros your PC just built, with no reload. This happens by itself after a "
                 .. "request; use it if you missed the sound.", "GameFontDisableSmall",
          WIDTH - 2 * LEFT, LEFT, -298, 30)

    divider(frame, -336)
    frame.toggle = button(frame, "Advanced  >>", 130, LEFT, -346, function()
        setExpanded(not frame.expanded)
    end)
    local advanced = CreateFrame("Frame", nil, frame)
    advanced:SetPoint("TOPLEFT", frame, "TOPLEFT", 0, -384)
    advanced:SetSize(WIDTH, HEIGHT_EXPANDED - 384)
    frame.advanced = advanced
    addAdvanced(advanced)
    setExpanded(false)
    setMode(settings().mode)
    frame:Hide()
    return frame
end

local function openWindow(expanded)
    if not window then buildWindow() end
    if expanded ~= nil then setExpanded(expanded) end
    window:Show()
end

local function toggleWindow()
    if window and window:IsShown() then window:Hide() else openWindow(false) end  -- the simple view
end
core.toggleWindow = toggleWindow

-- Minimap button ------------------------------------------------------------------------------------

local function place(self, angle)
    local radians = math.rad(angle)
    local radius = Minimap:GetWidth() / 2 + 5
    self:ClearAllPoints()
    self:SetPoint("CENTER", Minimap, "CENTER", math.cos(radians) * radius, math.sin(radians) * radius)
end

-- Angle (degrees, 0 = right, 90 = up) from the minimap's center to the mouse cursor.
function core.cursorAngle()
    local mx, my = Minimap:GetCenter()
    local cx, cy = GetCursorPosition()
    local scale = Minimap:GetEffectiveScale()
    return math.deg(math.atan2(cy / scale - my, cx / scale - mx)) % 360
end

local function createMinimapButton()
    local b = CreateFrame("Button", "MacroForgeMinimapButton", Minimap)
    minimapButton = b
    b:SetSize(31, 31)
    b:SetFrameStrata("MEDIUM")
    b:SetFrameLevel(8)
    b:RegisterForClicks("LeftButtonUp", "RightButtonUp")
    b:RegisterForDrag("LeftButton")

    local background = b:CreateTexture(nil, "BACKGROUND")
    background:SetSize(20, 20)
    background:SetTexture("Interface\\Minimap\\UI-Minimap-Background")
    background:SetPoint("TOPLEFT", b, "TOPLEFT", 7, -5)
    local icon = b:CreateTexture(nil, "ARTWORK")
    icon:SetSize(17, 17)
    icon:SetTexture(ICON)
    if not icon:GetTexture() then icon:SetTexture(FALLBACK_ICON) end
    icon:SetTexCoord(0.07, 0.93, 0.07, 0.93)
    icon:SetPoint("TOPLEFT", b, "TOPLEFT", 7, -6)
    local border = b:CreateTexture(nil, "OVERLAY")
    border:SetSize(53, 53)
    border:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")
    border:SetPoint("TOPLEFT", b, "TOPLEFT", 0, 0)
    b:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight")

    b:SetScript("OnClick", function(_, which)
        if which == "RightButton" then openWindow(true) else toggleWindow() end
    end)
    b:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_LEFT")
        GameTooltip:AddLine("MacroForge")
        GameTooltip:AddLine("Left-click: ask for a macro", 1, 1, 1)
        GameTooltip:AddLine("Right-click: advanced options", 1, 1, 1)
        GameTooltip:AddLine("Drag: move this button", 0.7, 0.7, 0.7)
        GameTooltip:Show()
    end)
    b:SetScript("OnLeave", function() GameTooltip:Hide() end)
    b:SetScript("OnDragStart", function(self)
        self:SetScript("OnUpdate", function(button)
            settings().angle = core.cursorAngle()
            place(button, settings().angle)
        end)
    end)
    b:SetScript("OnDragStop", function(self) self:SetScript("OnUpdate", nil) end)

    place(b, settings().angle or 210)
    applyButtonVisibility()
end

local events = CreateFrame("Frame")
events:RegisterEvent("PLAYER_LOGIN")  -- saved settings are available from here on
events:SetScript("OnEvent", createMinimapButton)
