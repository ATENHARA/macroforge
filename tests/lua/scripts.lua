-- Runs script snippets against a fake game and reports what they printed and which actions they took.
--   luajit tests/lua/scripts.lua <file>   (each line: "<runs>\t<hex of the Lua code>")
-- Output per snippet: "ok|error<TAB>hex(prints)<TAB>hex(calls)". Undefined names are an error, as a
-- mistyped function would be in the game (MF_ names start out as nil, as globals do).

math.randomseed(7)

local function hex(text)
    return (tostring(text):gsub(".", function(c) return string.format("%02x", c:byte()) end))
end

local function unhex(text)
    return (text:gsub("%x%x", function(h) return string.char(tonumber(h, 16)) end))
end

local function new_env()
    local prints, calls = {}, {}
    local function record(name)
        return function(...)
            local parts = {}
            for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
            calls[#calls + 1] = name .. "(" .. table.concat(parts, ",") .. ")"
        end
    end
    local stubs = {
        print = function(...)
            local parts = {}
            for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
            prints[#prints + 1] = table.concat(parts, " ")
        end,
        math = math, string = string, table = table, select = select, type = type,
        tostring = tostring, tonumber = tonumber, pairs = pairs, ipairs = ipairs, next = next,
        unpack = unpack, pcall = pcall, error = error, assert = assert,
        random = math.random, format = string.format, floor = math.floor, abs = math.abs,
        max = math.max, min = math.min, strsub = string.sub, strlower = string.lower,
        strupper = string.upper, tinsert = table.insert, date = function() return "12:00" end,
        time = function() return 1700000000 end, GetTime = function() return 1234.5 end,
        C_Map = {
            GetBestMapForUnit = function() return 1429 end,
            GetPlayerMapPosition = function() return { x = 0.5, y = 0.25 } end,
        },
        GetZoneText = function() return "Elwynn Forest" end,
        C_Container = { GetContainerNumFreeSlots = function() return 3 end },
        GetInventoryItemDurability = function(slot)
            if slot <= 3 then return 80, 100 end
        end,
        GetNetStats = function() return 0, 0, 35, 60 end,
        GetFramerate = function() return 60.2 end,
        UnitXP = function() return 50 end, UnitXPMax = function() return 100 end,
        GetMoney = function() return 10203 end,
        GetMoneyString = function() return "1g 2s 3c" end,
        UnitExists = function() return true end, UnitName = function() return "Bob" end,
        UnitLevel = function() return 30 end, UnitClass = function() return "Warlock", "WARLOCK", 9 end,
        UnitAffectingCombat = function() return false end,
        GetCVar = function() return "1" end,
        SetCVar = record("SetCVar"), DoEmote = record("DoEmote"), C_PetInfo = { PetRename = record("PetRename") },
        SetRaidTarget = record("SetRaidTarget"), PlaySound = record("PlaySound"),
    }
    local env = setmetatable({}, { __index = function(_, key)
        local value = stubs[key]
        if value ~= nil then return value end
        if type(key) == "string" and key:find("^MF_") then return nil end
        error("undefined global " .. tostring(key), 2)
    end })
    return env, prints, calls
end

local handle = assert(io.open(arg[1], "rb"))
for line in handle:lines() do
    local runs, code = line:match("^(%d+)\t(%x+)$")
    local env, prints, calls = new_env()
    local fn, err = loadstring(unhex(code))
    local status = "ok"
    if not fn then
        status, err = "error", "does not compile: " .. tostring(err)
    else
        setfenv(fn, env)
        for _ = 1, tonumber(runs) do
            local ok, problem = pcall(fn)
            if not ok then status, err = "error", problem break end
        end
    end
    print(status, hex(err or table.concat(prints, "\n")), hex(table.concat(calls, ";")))
end
