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
-- Kalibrace se NIKDY nespousti sama: kazdy krok ulozi tu polohu, ve ktere
-- senzor prave je, takze prvni podnet musi dat clovek tlacitkem z Home
-- Assistantu.  gwctl polozi prikaz do /tmp/gwarm.<devId> a odtud se odesle ve
-- chvili, kdy se uzel ozve - to je jedina chvile, kdy prokazatelne nespi.
--
-- Nazev prikazu, kterym se krok potvrzuje, neni znamy: v zakladne zadny takovy
-- retezec neni (rozumi mu az firmware uzlu) a `cal1` uzel ignoruje - overeno
-- 2026-08-14 na zivem HW.  Po rucnim podnetu proto knihovna prochazi seznam
-- kandidatu, jeden na kazdou dalsi zadost, a jakmile uzel odpovi "calXdone",
-- prestane a zapise, ktery prikaz zabral.  Neznamy prikaz uzel jen zahodi.

local um01 = {}

local DEV_TYPE = "um01"

-- Znacka odlozeneho prikazu od gwctl, viz ws02-14.lua.
local ARM_PREFIX = "/tmp/gwarm."

-- Kandidati na prikaz kroku kalibrace; %s je cislo kroku.  Poradi je podle
-- pravdepodobnosti: `cal` pouzivaji ws02 i ds02, tvar `set=<vlastnost>,<hodnota>`
-- zna cl01 i ts01.
local PROBE = { "cal", "cal%s", "cal=%s", "set=cal,%s", "calibrate", "startcal" }

-- Nejkratsi odstup dvou pokusu.  Zadosti chodi v davkach po sekunde, takze bez
-- nej by se cely seznam projel driv, nez uzel staci odpovedet.
local PROBE_DELAY = 6

-- devId -> { step = cislo kroku, index = dalsi kandidat, last = cas pokusu }.
-- Zaznam vznika az rucnim prikazem, cimz je zaroven dana podminka, ze se bez
-- podnetu uzivatele nezkousi nic.
local state = {}

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

-- Vyzkouset dalsiho kandidata na potvrzeni kroku kalibrace.
function um01.probe(devId, step)
    local item = state[devId]
    if item == nil then
        return
    end
    if item.step ~= step then
        item = { step = step, index = 1, last = 0 }
        state[devId] = item
    end
    if item.index > #PROBE or os.time() - item.last < PROBE_DELAY then
        return
    end
    local command = string.format(PROBE[item.index], step)
    cloudLog.warn(
        "um01 {} krok {} zkousim {} ({}/{})",
        devId, step, command, item.index, #PROBE
    )
    item.last = os.time()
    item.index = item.index + 1
    um01.execute_ule_command(devId, command)
    if item.index > #PROBE then
        cloudLog.warn("um01 {} krok {} - zadny kandidat nezabral", devId, step)
    end
end

function um01.on_ule_event(devType, devId, url, payload)
    if devType ~= DEV_TYPE then
        return
    end
    local value = tostring(payload)

    if string.match(value, "^cal%d?done$") ~= nil then
        os.remove(ARM_PREFIX .. devId)
        local item = state[devId]
        local command = "?"
        if item ~= nil and item.index > 1 then
            command = string.format(PROBE[item.index - 1], item.step)
        end
        state[devId] = nil
        cloudLog.warn("um01 {} kalibrace {} - zabral prikaz {}", devId, value, command)
        return
    end

    -- Rucne vyzadany prikaz ma prednost; zaroven je to jediny okamzik, kterym
    -- uzivatel zkouseni povoluje.
    local armed = take_armed(devId)
    if armed ~= nil and armed ~= "" then
        cloudLog.warn("um01 odlozeny prikaz {} {}", devId, armed)
        if armed == "recal" then
            state[devId] = nil
        else
            state[devId] = { step = string.sub(armed, 4, 4), index = 1, last = os.time() }
        end
        um01.execute_ule_command(devId, armed)
        return
    end

    local step = string.match(value, "^cal(%d)req$")
    if step ~= nil then
        um01.probe(devId, step)
    end
end

function um01.on_cloud_event(event, data)
    if event ~= "device.command" or data.deviceType ~= DEV_TYPE then
        return
    end
    um01.execute_ule_command(data.deviceId, data.command)
end

return um01
