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
--   dmoff / dmon - demontaz senzoru z drzaku
--
-- Kalibrace se NIKDY nespousti sama: kazdy krok ulozi tu polohu, ve ktere
-- senzor prave je, takze prvni podnet musi dat clovek tlacitkem z Home
-- Assistantu.  gwctl polozi prikaz do /tmp/gwarm.<devId> a odtud se odesle ve
-- chvili, kdy se uzel ozve - to je jedina chvile, kdy prokazatelne nespi.
--
-- Nazev prikazu, kterym se krok potvrzuje, nikde neni.  Overeno 2026-08-14:
-- v zadnem oddilu flash zakladny (tram, coco, oba kernely, recovery, obe data
-- partition) zadny takovy retezec neni - zakladna text jen prepolsi a rozumi mu
-- az firmware uzlu.  Na `verreq` uzel odpovi do sekundy, takze prikazy prijima;
-- neznamemu prikazu ale nic nenamitne, /api/v1/bs/sink/unknown mlci.
--
-- Zbyva tedy projit prostor systematicky.  Kandidati se skladaji ze zakladu a
-- tvaru podle gramatiky, kterou firmware pouziva jinde: holy prikaz (`cal`,
-- `sirenon`), `set=<vlastnost>,<hodnota>` (cl01, ts01) a `mset=<a>;<b>` (ts01).
-- Zamerne tu nejsou obecna slova jako reset nebo clear, aby nesla omylem
-- vyvolat tovarni nastaveni uzlu.

local um01 = {}

local DEV_TYPE = "um01"

-- Znacka odlozeneho prikazu od gwctl, viz ws02-14.lua.
local ARM_PREFIX = "/tmp/gwarm."

-- @ = zaklad, # = cislo kroku.
local BASES = {
    "cal", "calib", "calibr", "calibrate", "calpos",
    "pos", "refpos", "setpos", "learn", "teach",
}
local FORMS = {
    "@", "@#", "@=#", "@ #", "@,#", "@:#", "@_#",
    "set=@,#", "mset=@;#", "@start", "@#start", "@on",
    "@ok", "@set", "@do", "@go", "@now", "@#on",
}

-- Nejkratsi odstup dvou pokusu.  Zadosti chodi v davkach po sekunde, takze bez
-- nej by se cely seznam projel driv, nez uzel staci odpovedet.
local PROBE_DELAY = 5

local COUNT = #BASES * #FORMS

local function candidate(index, step)
    local base = BASES[math.floor((index - 1) / #FORMS) + 1]
    local form = FORMS[(index - 1) % #FORMS + 1]
    local text = form:gsub("@", base)
    text = text:gsub("#", step)
    return text
end

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
    if item.index > COUNT or os.time() - item.last < PROBE_DELAY then
        return
    end
    local command = candidate(item.index, step)
    cloudLog.warn(
        "um01 {} krok {} zkousim {} ({}/{})", devId, step, command, item.index, COUNT
    )
    item.last = os.time()
    item.index = item.index + 1
    um01.execute_ule_command(devId, command)
    if item.index > COUNT then
        cloudLog.warn("um01 {} krok {} - vsech {} kandidatu selhalo", devId, step, COUNT)
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
            command = candidate(item.index - 1, item.step)
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
