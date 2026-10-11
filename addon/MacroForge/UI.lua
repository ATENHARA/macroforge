-- The MacroForge minimap button and window: a front end for the same actions as the /mf commands.
-- It only calls MacroForgeCore (MacroForge.lua); it never touches macros itself.
local core = MacroForgeCore

local ICON = "Interface\\Icons\\INV_Misc_Note_06"
local FALLBACK_ICON = 134400  -- the question mark, present in every client
local WIDTH, HEIGHT_COLLAPSED, HEIGHT_EXPANDED = 380, 412, 632
local LEFT = 14

-- Saved between sessions (SavedVariables): where the button sits and whether it is hidden.
local function settings()
    if type(MacroForgeUI) ~= "table" then MacroForgeUI = {} end
    return MacroForgeUI
end

-- A child object that a template provides (a title, a checkbox label), or nil if it has none.
-- Text that came from saved data is shown with '|' doubled so it cannot render colors or links.
local function plain(text) return (tostring(text):gsub("|", "||")) end

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
local panel, refreshPanel  -- the side panel (built on first use) and the function that redraws it
local lastStatus = "Ready."
local MODE = {
    macro = { heading = "Ask for a macro", idle = "Ready.", ideas = "Macro ideas",
              hint = "Ask for a macro, or ask a question about macros...",
              desc = "Describe what you want in plain English. Your PC builds it and the macro appears "
                     .. "here automatically, usually within a minute." },
    script = { heading = "Ask for a script", ideas = "Script examples",
               hint = "Ask for a script, or ask a question about scripts...",
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
    if window.ideasButton then window.ideasButton:SetText(MODE[mode].ideas) end
    if window.hint then window.hint:SetText(MODE[mode].hint) end
    if panel and panel:IsShown() and panel.view == "ideas" then refreshPanel() end
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

-- Side panel: macro ideas / script examples and the request history. It opens beside the window
-- only when a button is clicked, and one panel serves both lists.
local PANEL_WIDTH, PANEL_HEIGHT, CARD_WIDTH, GAP = 340, 440, 296, 8

local function textHeight(fs, fallback)
    local height = fs:GetStringHeight()
    return (type(height) == "number" and height > 0) and height or fallback
end

local function fontString(parent, font, x, y, width)
    local fs = parent:CreateFontString(nil, "ARTWORK", font)
    fs:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
    fs:SetWidth(width)
    fs:SetJustifyH("LEFT")
    fs:SetJustifyV("TOP")
    return fs
end

local function shade(parent, r, g, b, a)
    local t = parent:CreateTexture(nil, "BACKGROUND")
    t:SetColorTexture(r, g, b, a)
    return t
end

-- "5 min ago" for a saved time (0 = unknown).
local function ago(stamp)
    if type(stamp) ~= "number" or stamp <= 0 then return "" end
    local seconds = math.max(0, time() - stamp)
    if seconds < 60 then return "just now" end
    if seconds < 3600 then return ("%d min ago"):format(seconds / 60) end
    if seconds < 86400 then return ("%d h ago"):format(seconds / 3600) end
    return ("%d d ago"):format(seconds / 86400)
end

-- One macro idea or script example, drawn like an entry of the game's macro page.
local function newCard(parent)
    local card = CreateFrame("Frame", nil, parent)
    card:SetWidth(CARD_WIDTH)
    shade(card, 1, 1, 1, 0.06):SetAllPoints()
    card.icon = card:CreateTexture(nil, "ARTWORK")
    card.icon:SetSize(36, 36)
    card.icon:SetPoint("TOPLEFT", card, "TOPLEFT", 8, -8)
    card.name = fontString(card, "GameFontNormal", 52, -9, CARD_WIDTH - 60)
    card.note = fontString(card, "GameFontDisableSmall", 52, -28, CARD_WIDTH - 60)
    card.why = fontString(card, "GameFontHighlightSmall", 8, -50, CARD_WIDTH - 16)
    card.box = shade(card, 0, 0, 0, 0.55)
    card.body = fontString(card, "GameFontHighlightSmall", 12, 0, CARD_WIDTH - 24)
    card.add = newFrame("Button", nil, card, "UIPanelButtonTemplate")
    card.add:SetSize(80, 22)
    return card
end

local function fillCard(card, idea, y)
    card.icon:SetTexture(idea.icon)
    card.name:SetText(plain(idea.name))
    local level = tonumber(idea.level) or 0
    if level > 0 then
        card.note:SetText(("Needs level %d"):format(level))
        if (UnitLevel("player") or 0) < level then card.note:SetTextColor(1, 0.4, 0.4)
        else card.note:SetTextColor(0.6, 0.6, 0.6) end
    else
        card.note:SetText(idea.script and "Any character" or "Any level")
        card.note:SetTextColor(0.6, 0.6, 0.6)
    end
    card.why:SetText(plain(idea.why))
    local whyHeight = textHeight(card.why, 24)
    card.body:SetText(plain(idea.body))
    local bodyHeight = textHeight(card.body, 36)
    local boxTop = -(50 + whyHeight + 6)
    card.box:ClearAllPoints()
    card.box:SetPoint("TOPLEFT", card, "TOPLEFT", 8, boxTop)
    card.box:SetSize(CARD_WIDTH - 16, bodyHeight + 8)
    card.body:ClearAllPoints()
    card.body:SetPoint("TOPLEFT", card, "TOPLEFT", 12, boxTop - 4)
    local buttonTop = boxTop - bodyHeight - 8 - 6
    card.add:ClearAllPoints()
    card.add:SetPoint("TOPRIGHT", card, "TOPRIGHT", -8, buttonTop)
    local function showState()
        if core.ideaAdded(idea) then card.add:SetText("Added"); card.add:Disable()
        else card.add:SetText("Add"); card.add:Enable() end
    end
    showState()
    card.add:SetScript("OnClick", function()
        core.addIdea(idea)
        showState()
    end)
    card:SetHeight(-buttonTop + 22 + 8)
    card:ClearAllPoints()
    card:SetPoint("TOPLEFT", card:GetParent(), "TOPLEFT", 0, -y)
    card:Show()
    return card:GetHeight() + GAP
end

-- One earlier request with its reply; clicking the row shows the whole reply.
local function newRow(parent)
    local row = CreateFrame("Frame", nil, parent)
    row:SetWidth(CARD_WIDTH)
    row:EnableMouse(true)
    shade(row, 1, 1, 1, 0.06):SetAllPoints()
    row.head = fontString(row, "GameFontNormalSmall", 8, -6, CARD_WIDTH - 16)
    row.text = fontString(row, "GameFontHighlightSmall", 8, -22, CARD_WIDTH - 16)
    row.reply = fontString(row, "GameFontDisableSmall", 8, 0, CARD_WIDTH - 16)
    row.again = newFrame("Button", nil, row, "UIPanelButtonTemplate")
    row.again:SetSize(80, 22)
    row.again:SetText("Use again")
    return row
end

local function fillRow(row, entry, y, expanded, toggle, again)
    row.head:SetText(("%s, %s"):format(entry.mode == "script" and "Script" or "Macro", ago(entry.time)))
    row.text:SetText(plain(entry.text))
    local textTop = textHeight(row.text, 12)
    local reply = entry.reply and plain(entry.reply) or "No reply yet."
    if not expanded and #reply > 140 then reply = reply:sub(1, 137) .. "..." end
    row.reply:SetText(reply)
    row.reply:ClearAllPoints()
    row.reply:SetPoint("TOPLEFT", row, "TOPLEFT", 8, -(22 + textTop + 6))
    local replyHeight = textHeight(row.reply, 12)
    local buttonTop = -(22 + textTop + 6 + replyHeight + 6)
    row.again:ClearAllPoints()
    row.again:SetPoint("TOPRIGHT", row, "TOPRIGHT", -8, buttonTop)
    row.again:SetScript("OnClick", function() again(entry) end)
    row:SetScript("OnMouseUp", function() toggle(entry.id) end)
    row:SetHeight(-buttonTop + 22 + 6)
    row:ClearAllPoints()
    row:SetPoint("TOPLEFT", row:GetParent(), "TOPLEFT", 0, -y)
    row:Show()
    return row:GetHeight() + GAP
end

-- Put a request back in the box so it can be sent again (or changed first).
local function useAgain(entry)
    setMode(entry.mode)
    window.edit:SetText(entry.text)
    window.edit:SetFocus()
    setStatus("Request loaded. Change it if you like, then press Send.")
end

local function placePanel()
    panel:ClearAllPoints()
    local right, screen = window:GetRight(), UIParent:GetWidth()
    if type(right) == "number" and type(screen) == "number" and right + PANEL_WIDTH + 4 > screen then
        panel:SetPoint("TOPRIGHT", window, "TOPLEFT", -2, 0)  -- no room on the right: open on the left
    else
        panel:SetPoint("TOPLEFT", window, "TOPRIGHT", 2, 0)
    end
end

local PANEL_TEXT = {
    macro = { title = "Macro ideas", help = "Made for your class. Add puts one on this character.",
              empty = "There are no ideas for your class yet." },
    script = { title = "Script examples", help = "Short scripts to start from. Add puts one in your account-wide macros.",
               empty = "There are no script examples." },
    history = { title = "History", help = "Your recent requests. Click one to read the whole reply.",
                empty = "No requests yet. Ask for a macro or a script and it will show up here." },
}

refreshPanel = function()
    local view = panel.view
    local key = view == "history" and "history" or window.mode
    local text = PANEL_TEXT[key]
    local title = part(panel, "TitleText")
    if title then title:SetText(text.title) end
    panel.help:SetText(text.help)
    local items = view == "history" and core.history() or core.ideas(window.mode)
    local pool = view == "history" and panel.rows or panel.cards
    for _, list in ipairs({ panel.rows, panel.cards }) do
        for _, widget in ipairs(list) do widget:Hide() end
    end
    panel.empty:SetText(text.empty)
    panel.empty:SetShown(#items == 0)
    local y = 0
    for index, item in ipairs(items) do
        if not pool[index] then
            pool[index] = view == "history" and newRow(panel.content) or newCard(panel.content)
        end
        if view == "history" then
            y = y + fillRow(pool[index], item, y, panel.open == item.id, function(id)
                panel.open = (panel.open ~= id) and id or nil
                refreshPanel()
            end, useAgain)
        else
            y = y + fillCard(pool[index], item, y)
        end
    end
    panel.content:SetHeight(math.max(y, 1))
end

local function buildPanel()
    local frame, templated = newFrame("Frame", "MacroForgeSidePanel", UIParent, "BasicFrameTemplateWithInset")
    panel = frame
    frame:SetSize(PANEL_WIDTH, PANEL_HEIGHT)
    frame:SetFrameStrata("DIALOG")
    frame:EnableMouse(true)
    frame:SetClampedToScreen(true)
    if not templated then shade(frame, 0, 0, 0, 0.85):SetAllPoints() end
    if UISpecialFrames then table.insert(UISpecialFrames, "MacroForgeSidePanel") end  -- Esc closes it
    frame.help = label(frame, "", "GameFontHighlightSmall", PANEL_WIDTH - 2 * LEFT, LEFT, -30, 28)
    local scroll = newFrame("ScrollFrame", "MacroForgePanelScroll", frame, "UIPanelScrollFrameTemplate")
    scroll:SetPoint("TOPLEFT", frame, "TOPLEFT", LEFT - 2, -62)
    scroll:SetSize(CARD_WIDTH, PANEL_HEIGHT - 76)
    frame.content = CreateFrame("Frame", nil, scroll)
    frame.content:SetSize(CARD_WIDTH, 1)
    scroll:SetScrollChild(frame.content)
    frame.empty = label(frame.content, "", "GameFontDisable", CARD_WIDTH - 16, 8, -8, 60)
    frame.cards, frame.rows = {}, {}
    frame:Hide()
    return frame
end

-- Open the panel on a view ("ideas" or "history"); clicking the same button again closes it.
local function showPanel(view)
    if not panel then buildPanel() end
    if panel:IsShown() and panel.view == view then panel:Hide(); return end
    panel.view = view
    placePanel()
    refreshPanel()
    panel:Show()
end

core.onHistory = function()
    if panel and panel:IsShown() and panel.view == "history" then refreshPanel() end
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
    frame:SetScript("OnDragStop", function(self)
        self:StopMovingOrSizing()
        if panel and panel:IsShown() then placePanel() end  -- the side may have changed
    end)
    frame:SetScript("OnHide", function() if panel then panel:Hide() end end)
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
    -- a grey hint over the empty box, so it is clear that questions are fine too
    frame.hint = label(scroll, MODE.macro.hint, "GameFontDisableSmall", WIDTH - 2 * LEFT - 44, 4, -4)
    -- where the new macro is saved: this character (the default) or all of them
    local everyone = newFrame("CheckButton", nil, frame, "UICheckButtonTemplate")
    everyone:SetPoint("TOPLEFT", frame, "TOPLEFT", LEFT + 50, -187)
    everyone:SetSize(22, 22)
    local everyoneText = part(everyone, "Text", "text")
    if everyoneText then everyoneText:SetText("Save for all characters")
    else label(frame, "Save for all characters", "GameFontNormalSmall", 140, LEFT + 76, -192) end
    everyone:SetChecked(settings().scope == "account")
    everyone:SetScript("OnClick", function(self)
        settings().scope = self:GetChecked() and "account" or nil
    end)
    frame.everyone = everyone
    edit:SetScript("OnTextChanged", function(self)
        counter:SetText(("%d/500"):format(#self:GetText()))
        frame.hint:SetShown(self:GetText() == "")
    end)
    edit:SetScript("OnEscapePressed", function(self) self:ClearFocus() end)
    scroll:EnableMouse(true)
    scroll:SetScript("OnMouseDown", function() edit:SetFocus() end)
    button(frame, "Send request", 130, WIDTH - LEFT - 130, -188, send)
    frame.status = label(frame, lastStatus, "GameFontNormalSmall", WIDTH - 2 * LEFT, LEFT, -220, 30)

    divider(frame, -256)
    frame.ideasButton = button(frame, MODE.macro.ideas, 150, LEFT, -268, function() showPanel("ideas") end)
    button(frame, "History", 100, LEFT + 160, -268, function() showPanel("history") end)
    button(frame, "Apply new macros", 150, LEFT, -300, function()
        setStatus("Looking for new macros...")
        core.live()
    end)
    label(frame, "Loads macros your PC just built, with no reload. This happens by itself after a "
                 .. "request; use it if you missed the sound.", "GameFontDisableSmall",
          WIDTH - 2 * LEFT, LEFT, -330, 30)

    divider(frame, -368)
    frame.toggle = button(frame, "Advanced  >>", 130, LEFT, -378, function()
        setExpanded(not frame.expanded)
    end)
    local advanced = CreateFrame("Frame", nil, frame)
    advanced:SetPoint("TOPLEFT", frame, "TOPLEFT", 0, -416)
    advanced:SetSize(WIDTH, HEIGHT_EXPANDED - 416)
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
