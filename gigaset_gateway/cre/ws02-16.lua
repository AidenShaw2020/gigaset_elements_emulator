-- Knihovna pro okenni senzor ws02.  Stock firmware zadnou ws02 knihovnu nema,
-- takze na udalost "calreq" (senzor hlasi, ze nema platnou kalibraci) nikdo
-- neodpovidal a senzor se ptal donekonecna.
--
-- Slovnik udalosti overeny na zivem HW:
--   calreq  - senzor je nezkalibrovany a zada o kalibraci
--   caldone - kalibrace probehla uspesne
--   open / close / tilt - poloha okna, chodi az PO kalibraci
--   button  - parovaci tlacitko
--
-- Prikazy do senzoru:
--   cal    - soucasnou polohu si zapamatuj jako "zavreno" (odpovi caldone)
--   recal  - zahod soucasnou kalibraci
--
-- Oficialni navod k parovani (viz appka) je: otevri okno (probudi senzor),
-- zavri okno, AZ PAK stiskni tlacitko na senzoru - teprve tenhle stisk je
-- potvrzeni "ted je to ve spravne poloze".  Kdybychom na "calreq" odpovedeli
-- "cal" hned/automaticky, muzeme si takhle zapsat jako "zavreno" polohu, ve
-- ktere senzor jeste drzi v ruce nebo se teprve umist'uje - to je pravdepodobna
-- pricina hlaseneho problemu, kdy ws02 po case prestane hlasit open/close
-- (kalibrace je od zacatku spatne, tilt funguje dal protoze na kalibraci
-- nezavisi).
--
-- DULEZITE: kalibrace se proto NIKDY neposila sama jen kvuli uplynulemu casu.
-- Jedina spolehliva a jedina pripustna cesta je vyslovne potvrzeni uzivatelem
-- - bud stiskem tlacitka na senzoru (viz "button" nize), nebo rucnim prikazem
-- z Home Assistantu. Automaticky "timeout" fallback, ktery by kalibraci
-- odeslal sam bez lidskeho potvrzeni, je presne to chovani, ktere zpusobilo
-- puvodni problem, a nesmi se sem vratit.
-- POZOR: prikaz se MUSI posilat pres ule_command_send.  Zapis na JBus tema
-- ulecontrol (to dela i stock /usr/bin/calibrate.sh) UleApp zahodi, protoze
-- propousti jen prikazy ze sve vlastni tabulky (regon/regoff/reglist/delete).
--
-- "temp"/"press" tahle knihovna schvalne nezkousi: umi je jen uzly postavene
-- na um01 (viz UM01_FIRMWARE.md a um01-9.lua), skutecny ws02 na ne neodpovi
-- vubec. Kdyby pod timhle typem bezel prave takovy prevedeny uzel, prikazy
-- porad jdou poslat rucne pres ws02.execute_ule_command.

local ws02 = {}

local DEV_TYPE = "ws02"

-- Jak casto nejvyse pripomenout v logu, ze uzel ceka na stisk tlacitka - jen
-- log, zadny prikaz se odtud neposila (viz DULEZITE vyse).
local RETRY_DELAY = 120
-- Po tolika pripominkach uz jen dlouhy odstup, aby log nezahltily uzly bez
-- tlacitka ci nedostupne.
local MAX_TRIES = 3
local BACKOFF_DELAY = 3600

-- Znacka odlozeneho prikazu od gwctl.  ULE uzel vetsinu casu spi, takze prikaz
-- poslany v libovolny okamzik se k nemu nemusi dostat.  gwctl proto polozi
-- soubor a my prikaz odesleme az ve chvili, kdy se uzel sam ozve - tehdy je
-- prokazatelne vzhuru a ma otevrene prijimaci okno.
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

function ws02.execute_ule_command(devId, command)
    cloudLog.warn("ws02 ule_command_send {} {}", devId, command)
    ule_command_send(DEV_TYPE, devId, command)
end

-- Vynucena kalibrace na soucasne poloze.
function ws02.calibrate(devId)
    local item = entry(devId)
    item.last = os.time()
    item.tries = item.tries + 1
    ws02.execute_ule_command(devId, "cal")
end

function ws02.on_ule_event(devType, devId, url, payload)
    if devType ~= DEV_TYPE then
        return
    end
    local value = tostring(payload)

    if value == "caldone" then
        os.remove(ARM_PREFIX .. devId)
        state[devId] = { last = os.time(), tries = 0, awaiting = false }
        cloudLog.warn("ws02 kalibrace hotova {}", devId)
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
            -- "recal" kalibraci naopak rusi.  Kdybychom si tady poznamenali cas
            -- pokusu, zablokovali bychom si odpoved na calreq, ktery senzor
            -- posle hned potom - a prave ten je treba obslouzit okamzite.
            state[devId] = nil
        end
        cloudLog.warn("ws02 odlozeny prikaz {} {}", devId, armed)
        ws02.execute_ule_command(devId, armed)
        return
    end

    -- Parovaci tlacitko: podle navodu ho uzivatel mackne az PO fyzickem
    -- zavreni okna, takze kdyz prijde behem cekani na kalibraci, je to
    -- nejspolehlivejsi chvile na "cal" - poloha uz je jista.
    if value == "button" then
        local item = entry(devId)
        if item.awaiting then
            ws02.calibrate(devId)
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
        cloudLog.warn("ws02 kalibrace {} ceka na stisk tlacitka na senzoru", devId)
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
    cloudLog.warn("ws02 kalibrace {} porad ceka na stisk tlacitka na senzoru", devId)
end

function ws02.on_cloud_event(event, data)
    if event ~= "device.command" or data.deviceType ~= DEV_TYPE then
        return
    end
    -- Rucne vyzadana kalibrace ma prednost pred automatikou, tak se citac
    -- pokusu vynuluje a automatika hned nezacne posilat vlastni "cal".
    if data.command == "cal" or data.command == "recal" then
        state[data.deviceId] = { last = os.time(), tries = 0, awaiting = false }
    end
    ws02.execute_ule_command(data.deviceId, data.command)
end

return ws02
