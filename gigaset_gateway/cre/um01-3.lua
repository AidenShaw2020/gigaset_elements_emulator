-- Nezavisla implementace CRE rozhrani um01, univerzalniho senzoru na dvere
-- i okno.  Stock knihovna resi jen teplotu, na kalibraci neodpovida nikdo,
-- takze nezkalibrovany uzel posila "cal1req" donekonecna.
--
-- Slovnik udalosti:
--   cal1req  - senzor ceka na ulozeni ZAVRENE polohy
--   cal1done - prvni krok hotov
--   cal2req  - senzor ceka na ulozeni OTEVRENE polohy
--   cal2done - kalibrace hotova
--   open / close / tilt - poloha, chodi az po kalibraci
--
-- Na rozdil od ws02/ds02 se tady kalibrace NESPOUSTI sama.  Kazdy z obou kroku
-- ulozi jednu konkretni fyzickou polohu senzoru, kterou musi nastavit uzivatel;
-- automaticka odpoved by si zapamatovala tu, ve ktere senzor prave lezi.
-- Prikaz cal1 / cal2 proto prijde z Home Assistantu pres gwctl, ktery ho polozi
-- do /tmp/gwarm.<devId>, a odtud se odesle v okamziku, kdy se uzel sam ozve -
-- to je jedina chvile, kdy prokazatelne nespi a ma otevrene prijimaci okno.

local um01 = {}

local DEV_TYPE = "um01"

-- Znacka odlozeneho prikazu od gwctl, viz ws02-14.lua.
local ARM_PREFIX = "/tmp/gwarm."

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

function um01.execute_ule_command(devId, command)
    cloudLog.warn("um01 ule_command_send {} {}", devId, command)
    ule_command_send(DEV_TYPE, devId, command)
end

function um01.on_ule_event(devType, devId, url, payload)
    if devType ~= DEV_TYPE then
        return
    end
    local value = tostring(payload)

    if value == "cal1done" or value == "cal2done" or value == "caldone" then
        -- Prikaz dorazil, znacka uz jen ceka na to, az ji nekdo odesle znovu.
        os.remove(ARM_PREFIX .. devId)
        cloudLog.warn("um01 kalibrace {} potvrzena {}", devId, value)
        return
    end

    local armed = take_armed(devId)
    if armed ~= nil and armed ~= "" then
        cloudLog.warn("um01 odlozeny prikaz {} {}", devId, armed)
        um01.execute_ule_command(devId, armed)
    end
end

function um01.on_cloud_event(event, data)
    if event ~= "device.command" or data.deviceType ~= DEV_TYPE then
        return
    end
    um01.execute_ule_command(data.deviceId, data.command)
end

return um01
