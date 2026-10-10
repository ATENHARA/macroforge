-- Packs a message into 3-bit cells, the same way mforge/stripcodec.py unpacks them:
--   C7 1A, id (2 bytes), payload length (2 bytes), payload, then a Fletcher-16 checksum (2 bytes).
-- Plain arithmetic only: the game's Lua 5.1 has no bit operators.
MacroForgeCodec = {}

local function fletcher(bytes, first, last)
    local s1, s2 = 0, 0
    for i = first, last do
        s1 = (s1 + bytes[i]) % 255
        s2 = (s2 + s1) % 255
    end
    return s1, s2
end

-- Returns an array of cell values 0..7.
function MacroForgeCodec.Encode(id, payload)
    local length = #payload
    local bytes = { 0xC7, 0x1A, math.floor(id / 256) % 256, id % 256,
                    math.floor(length / 256) % 256, length % 256 }
    for i = 1, length do bytes[#bytes + 1] = payload:byte(i) end
    local s1, s2 = fletcher(bytes, 3, 6 + length)
    bytes[#bytes + 1] = s1
    bytes[#bytes + 1] = s2

    local cells, accumulator, bits = {}, 0, 0
    for i = 1, #bytes do
        accumulator = accumulator * 256 + bytes[i]
        bits = bits + 8
        while bits >= 3 do
            local shift = bits - 3
            cells[#cells + 1] = math.floor(accumulator / 2 ^ shift) % 8
            bits = shift
            accumulator = accumulator % 2 ^ bits
        end
    end
    if bits > 0 then cells[#cells + 1] = (accumulator * 2 ^ (3 - bits)) % 8 end
    return cells
end

-- Draws a message as colored squares in the top-left corner and takes a screenshot of it. The game
-- writes the picture to its Screenshots folder immediately (as a TGA while the format is switched),
-- which is how the PC-side watcher receives a request without any UI reload.
MacroForgeStrip = {}

local CELL_PX, PER_ROW = 8, 100

local function drawStrip(cells)
    local frame = CreateFrame("Frame", nil, UIParent)
    frame:SetFrameStrata("TOOLTIP")
    local _, physicalHeight = GetPhysicalScreenSize()
    -- one UI unit = one screen pixel, so the squares keep their size in the saved picture
    frame:SetScale((768 / physicalHeight) / UIParent:GetEffectiveScale())
    local rows = math.ceil(#cells / PER_ROW)
    frame:SetSize(PER_ROW * CELL_PX, rows * CELL_PX)
    frame:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 0, 0)
    local background = frame:CreateTexture(nil, "BACKGROUND")
    background:SetAllPoints()
    background:SetColorTexture(0, 0, 0, 1)
    for index, cell in ipairs(cells) do
        local square = frame:CreateTexture(nil, "OVERLAY")
        square:SetSize(CELL_PX, CELL_PX)
        local column, row = (index - 1) % PER_ROW, math.floor((index - 1) / PER_ROW)
        square:SetPoint("TOPLEFT", frame, "TOPLEFT", column * CELL_PX, -row * CELL_PX)
        square:SetColorTexture(math.floor(cell / 4) % 2, math.floor(cell / 2) % 2, cell % 2, 1)
    end
    return frame
end

-- Sends `payload` (a string). `onDone(ok, err)` runs after the picture was taken.
function MacroForgeStrip.Send(id, payload, onDone)
    local cells = MacroForgeCodec.Encode(id % 65536, payload)
    local previous = GetCVar("screenshotFormat")
    pcall(SetCVar, "screenshotFormat", "tga")
    local frame = drawStrip(cells)
    C_Timer.After(0.5, function()  -- let the squares render before the picture is taken
        local ok, err = pcall(Screenshot)
        C_Timer.After(1.5, function()
            frame:Hide()
            if previous and previous ~= "tga" then pcall(SetCVar, "screenshotFormat", previous) end
            if onDone then onDone(ok, err) end
        end)
    end)
    return #cells
end
