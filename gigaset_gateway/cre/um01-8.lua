-- Nezavisla implementace CRE rozhrani um01, univerzalniho senzoru na dvere
-- i okno.  Stock knihovna resi jen teplotu, na kalibraci neodpovida nikdo,
-- takze nezkalibrovany uzel posila "cal1req" donekonecna.
--
-- Slovnik udalosti:
--   cal1req     - senzor ceka na ulozeni ZAVRENE polohy
--   cal1started - prvni krok prijat a probiha
--   cal1done    - prvni krok hotov
--   cal2req     - senzor ceka na ulozeni OTEVRENE polohy
--   cal2started - druhy krok prijat a probiha
--   cal2done    - kalibrace hotova
--   calreused   - senzor pouzil drive ulozenou kalibraci
--   open / close / tilt / preopen - poloha, chodi az po kalibraci
--   dmon / dmoff - montaz a demontaz senzoru z drzaku
--
-- Prikazy do senzoru:
--   cal    - uloz soucasnou polohu jako ZAVRENO (prvni krok)
--   cal2   - uloz soucasnou polohu jako OTEVRENO (druhy krok)
--   recal  - zahod kalibraci
--
-- Nazvy prikazu jsou vyctene primo z firmwaru uzlu (EFM32G Gecko, SWD, tabulka
-- retezcu na 0x172ec).  Prvni krok se jmenuje "cal", NIKOLI "cal1" - retezec
-- "cal1" je jen soucasti nazvu udalosti a stavu, jako prikaz ve firmwaru uzlu
-- neexistuje.  Stejna asymetrie je u ws02, kde aplikace posila "closed", ale na
-- uzel jde "cal".
--
-- Kalibrace se NIKDY nespousti sama: kazdy krok ulozi tu polohu, ve ktere
-- senzor prave je, takze oba podnety musi dat clovek tlacitkem z Home
-- Assistantu.  gwctl polozi prikaz do /tmp/gwarm.<devId> a odtud se odesle ve
-- chvili, kdy se uzel ozve - to je jedina chvile, kdy prokazatelne nespi.

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

    if string.match(value, "^cal%d?done$") ~= nil then
        os.remove(ARM_PREFIX .. devId)
        cloudLog.warn("um01 {} kalibrace {}", devId, value)
        return
    end

    -- Uzel je prave vzhuru, takze je to jedina spolehliva chvile na odeslani
    -- rucne vyzadaneho prikazu.  Zaroven je to jediny okamzik, kdy uzivatel
    -- kalibraci povoluje - bez jeho podnetu se neposila nic.
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
