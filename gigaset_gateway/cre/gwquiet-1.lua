-- Umlci diagnosticke logovani pravidloveho stroje na urovnich "debug" a "info".
--
-- Zakladna otevira pro kazdou zpravu do cloudu nove TLS spojeni a frontu
-- odbavuje zhruba jednu zpravu za sekundu.  Diagnosticke logy tvorily kolem
-- 15 % provozu a odsouvaly stavy senzoru i o desitky sekund.  Naprostou
-- vetsinu jich posilaji stock pravidla (homeleaving, homecoming,
-- siren_patterns_manager, intrusion), takze se to neda vyresit uvnitr
-- vlastnich knihoven.
--
-- Puvodni knihovna cloudLog zustava nedotcena; jen se za behu vymeni polozky
-- v jeji globalni tabulce.  To funguje proto, ze zadny modul si referenci na
-- cloudLog neuklada do lokalni promenne a vsechna volani jdou pres globalni
-- tabulku.  Urovne "warn" a "error" se nemeni, takze si zachovavaji i spravny
-- odkaz na soubor a radek.

local gwquiet = {}

local SILENT_LEVELS = { debug = true, info = true }

local function discard()
end

local function silence()
    if type(cloudLog) ~= "table" or cloudLog.gwquiet ~= nil then
        return
    end
    cloudLog.gwquiet = true

    for level in pairs(SILENT_LEVELS) do
        cloudLog[level] = discard
    end

    -- logWithLevel dostava uroven az jako argument, takze se musi filtrovat za
    -- behu.  Volajici se tim posune o jeden ramec, ale zadne stock pravidlo
    -- tuto cestu nepouziva.
    local forward = cloudLog.logWithLevel
    if type(forward) == "function" then
        cloudLog.logWithLevel = function(level, ...)
            if SILENT_LEVELS[level] then
                return
            end
            return forward(level, ...)
        end
    end
end

-- Poradi nacitani knihoven neni dane, takze pri nacteni tohoto modulu jeste
-- cloudLog existovat nemusi; on_start bezi az kdyz jsou nactene vsechny.
silence()

function gwquiet.on_start(module_name)
    silence()
end

return gwquiet
