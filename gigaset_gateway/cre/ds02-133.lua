-- Nezavisla implementace CRE rozhrani ds02.  Krome obsluhy kalibrace poskytuje
-- i vstupni bod ds02.buzzer_off, protoze ho volaji jina CRE pravidla a bez nej
-- by se nenacetla.  Chovani je shodne s ws02-13.lua, viz komentar tam.

local ds02 = {}

local DEV_TYPE = "ds02"

local RETRY_DELAY = 120
local MAX_TRIES = 3
local BACKOFF_DELAY = 3600

-- Znacka odlozeneho prikazu od gwctl, viz ws02-12.lua.
local ARM_PREFIX = "/tmp/gwarm."

-- devId -> { last = cas posledniho pokusu, tries = pocet pokusu bez caldone }
local state = {}

local function entry(devId)
    local item = state[devId]
    if item == nil then
        item = { last = 0, tries = 0 }
        state[devId] = item
    end
    return item
end

local function take_armed(devId)
    local path = ARM_PREFIX .. devId
    local handle = io.open(path, "r")
    if handle == nil then
        return nil
    end
    local command = handle:read("*l")
    handle:close()
    os.remove(path)
    return command
end

function ds02.buzzer_off(devId)
    ule_command_send(DEV_TYPE, devId, "sirenoff")
end

function ds02.execute_ule_command(devId, command)
    cloudLog.warn("ds02 ule_command_send {} {}", devId, command)
    ule_command_send(DEV_TYPE, devId, command)
end

-- Vynucena kalibrace na soucasne poloze.
function ds02.calibrate(devId)
    local item = entry(devId)
    item.last = os.time()
    item.tries = item.tries + 1
    ds02.execute_ule_command(devId, "cal")
end

function ds02.on_ule_event(devType, devId, url, payload)
    if devType ~= DEV_TYPE then
        return
    end
    local value = tostring(payload)

    if value == "caldone" then
        os.remove(ARM_PREFIX .. devId)
        state[devId] = { last = os.time(), tries = 0 }
        cloudLog.warn("ds02 kalibrace hotova {}", devId)
        return
    end

    local armed = take_armed(devId)
    if armed ~= nil and armed ~= "" then
        if armed == "cal" then
            local item = entry(devId)
            item.last = os.time()
            item.tries = 0
        else
            state[devId] = nil
        end
        cloudLog.warn("ds02 odlozeny prikaz {} {}", devId, armed)
        ds02.execute_ule_command(devId, armed)
        return
    end

    if value ~= "calreq" then
        return
    end

    local item = entry(devId)
    local delay = RETRY_DELAY
    if item.tries >= MAX_TRIES then
        delay = BACKOFF_DELAY
    end
    if os.time() - item.last < delay then
        return
    end
    if item.tries == MAX_TRIES then
        cloudLog.warn("ds02 kalibrace {} opakovane selhava, potreba rucni postup", devId)
    end
    ds02.calibrate(devId)
end

function ds02.on_cloud_event(event, data)
    if event ~= "device.command" or data.deviceType ~= DEV_TYPE then
        return
    end
    if data.command == "cal" or data.command == "recal" then
        state[data.deviceId] = { last = os.time(), tries = 0 }
    end
    ds02.execute_ule_command(data.deviceId, data.command)
end

return ds02
