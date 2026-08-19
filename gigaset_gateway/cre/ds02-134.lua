-- Nezavisla implementace CRE rozhrani ds02.  Krome obsluhy kalibrace poskytuje
-- i vstupni bod ds02.buzzer_off, protoze ho volaji jina CRE pravidla a bez nej
-- by se nenacetla.  Chovani je shodne s ws02-16.lua, viz komentar tam (vcetne
-- duvodu, proc se kalibrace posila VYHRADNE po stisku tlacitka nebo rucnim
-- prikazem z Home Assistantu, nikdy sama jen kvuli uplynulemu casu).

local ds02 = {}

local DEV_TYPE = "ds02"

-- Jak casto nejvyse pripomenout v logu, ze uzel ceka na stisk tlacitka - jen
-- log, zadny prikaz se odtud neposila.
local RETRY_DELAY = 120
-- Po tolika pripominkach uz jen dlouhy odstup.
local MAX_TRIES = 3
local BACKOFF_DELAY = 3600

-- Znacka odlozeneho prikazu od gwctl, viz ws02-12.lua.
local ARM_PREFIX = "/tmp/gwarm."

-- devId -> { last = cas posledniho pokusu, tries = pocet pokusu bez caldone }
local state = {}

local function entry(devId)
    local item = state[devId]
    if item == nil then
        item = { last = 0, tries = 0, awaiting = false }
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
        state[devId] = { last = os.time(), tries = 0, awaiting = false }
        cloudLog.warn("ds02 kalibrace hotova {}", devId)
        return
    end

    -- Uzel je prave vzhuru, takze je to jedina spolehliva chvile na odeslani
    -- rucne vyzadaneho prikazu. Ma prednost pred vsim nasledujicim, vcetne
    -- automatickeho zpracovani tlacitka nize.
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

    -- Parovaci tlacitko: podle navodu ho uzivatel mackne az PO fyzickem
    -- zavreni dveri/okna, takze kdyz prijde behem cekani na kalibraci, je to
    -- nejspolehlivejsi chvile na "cal" - poloha uz je jista.
    if value == "button" then
        local item = entry(devId)
        if item.awaiting then
            ds02.calibrate(devId)
        end
        return
    end

    if value ~= "calreq" then
        return
    end

    -- POZOR: tady se kalibrace NIKDY nesmi odeslat sama jen kvuli uplynulemu
    -- casu - jedine spolehlive potvrzeni polohy je stisk tlacitka (vyse) nebo
    -- rucni prikaz z Home Assistantu (on_cloud_event nize). Tahle vetev jen
    -- pripominkuje v logu, ze uzel na potvrzeni ceka.
    local item = entry(devId)
    local first_request = not item.awaiting
    item.awaiting = true
    if first_request then
        item.last = os.time()
        item.tries = 1
        cloudLog.warn("ds02 kalibrace {} ceka na stisk tlacitka na senzoru", devId)
        return
    end
    local delay = RETRY_DELAY
    if item.tries >= MAX_TRIES then
        delay = BACKOFF_DELAY
    end
    if os.time() - item.last < delay then
        return
    end
    item.last = os.time()
    item.tries = item.tries + 1
    cloudLog.warn("ds02 kalibrace {} porad ceka na stisk tlacitka na senzoru", devId)
end

function ds02.on_cloud_event(event, data)
    if event ~= "device.command" or data.deviceType ~= DEV_TYPE then
        return
    end
    if data.command == "cal" or data.command == "recal" then
        state[data.deviceId] = { last = os.time(), tries = 0, awaiting = false }
    end
    ds02.execute_ule_command(data.deviceId, data.command)
end

return ds02
