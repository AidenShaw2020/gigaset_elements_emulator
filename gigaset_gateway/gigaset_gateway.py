from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Any, Callable


ENDNODE_RE = re.compile(
    r"^/api/v1/endnode/(?P<type>[^/]+)/(?P<id>[^/]+)/sink/(?P<sink>[^/?]+)"
)
ENDNODE_ID_RE = re.compile(r"^[0-9a-fA-F]{10}$")
ENDNODE_TYPE_RE = re.compile(r"^[a-z]{2}[0-9]{2}$")
# Spolecne jmeno z certifikatu zakladny, zaroven jeji identifikator.
BASE_IDENTITY_RE = re.compile(r"^[0-9a-f]{16,64}$")
# Prikazy, ktere ULE koncovy uzel skutecne zna.  "moving" a "closed" ze
# SensorCommand v puvodni aplikaci jsou stavy cloud API a zadny senzor na ne
# nereaguje, proto tu nejsou.  Univerzalni senzor um01 kalibruje dvema kroky:
# prvni potvrzuje "cal", druhy "cal2".  Retezec "cal1" ve firmwaru uzlu jako
# prikaz NEEXISTUJE, je jen soucasti nazvu udalosti cal1req/cal1done.
SAFE_ENDNODE_COMMANDS = {"cal", "cal2", "recal", "verreq"}

# Popisky typu zarizeni pro Home Assistant.
DEVICE_NAMES = {
    "ws02": "Gigaset okenni senzor",
    "ds02": "Gigaset dvernni senzor",
    "um01": "Gigaset univerzalni senzor",
    "ps02": "Gigaset detektor pohybu",
    "bn01": "Gigaset tlacitko",
    "is01": "Gigaset sirena",
}

# Typy, ktere hlasi polohu, a jejich slovnik.  "tilt" je sklopene okno; chodi
# stejne jako open/close az po uspesne kalibraci.  "preopen" hlasi jen uzly
# postavene na um01 a znamena pootevreno.
POSITION_TYPES = {"ws02", "ds02", "um01"}
POSITION_PAYLOADS = {"open", "close", "tilt", "preopen"}

# Nazev a trida kontaktni entity podle typu uzlu.  um01 je univerzalni senzor,
# ktery se lepi na dvere i na okno, takze se hlasi obecnou tridou "opening".
CONTACT_KINDS = {
    "ws02": ("Window", "window"),
    "ds02": ("Door", "door"),
    "um01": ("Opening", "opening"),
}

# Udalost kalibrace -> stav, ktery se z ni ukaze v Home Assistantu.  ws02 a
# ds02 kalibruji jednim krokem (calreq/caldone), um01 dvema: prvni si pamatuje
# zavrenou polohu, druhy otevrenou.
CALIBRATION_STATES = {
    "calreq": "required",
    "caldone": "ok",
    "cal1req": "step1_required",
    "cal1started": "step1_running",
    "cal1done": "step1_done",
    "cal2req": "step2_required",
    "cal2started": "step2_running",
    "cal2done": "ok",
    "calreused": "ok",
}
CALIBRATION_OPTIONS = sorted(set(CALIBRATION_STATES.values()))

# Sinky, pod kterymi uzel hlasi teplotu.  Stejne nazvy pouzivaji i uzly, ktere
# zadnou teplotu neposilaji (ws02 pod "state" posila treba "calreq"), takze o
# tom, jestli o teplotu jde, rozhoduje az tvar hodnoty.
TEMPERATURE_SINKS = {"tp", "state", "temp"}

# Tlak hlasi jen univerzalni senzor, a to jen na vyzadani prikazem "press".
PRESSURE_SINK = "press"

# Zvukove vzory sireny is01, mapovane na ULE prikaz.  Format je
# "<celkova doba>,<sekvence>", kde sekvence strida delku tonu a ticha;
# pismeno je jednotka casu (m=0,001 c=0,01 d=0,1 s=1 a=10 h=100 k=1000 s).
# Hodnoty jsou prevzate z pravidel ve firmwaru zakladny.
#
# Vzory umi jen uzel s firmware >= 00250000 (prvnich 8 znaku "ver"); starsi
# kus na "pattern=" nereaguje a zustane ticho.
SIREN_PATTERN_OFF = "vypnuto"
SIREN_PATTERNS = {
    SIREN_PATTERN_OFF: "patternoff",
    "zvonek": "pattern=1s,4d1d5d",
    "poplach": "pattern=2h,2D2d",
    "test": "pattern=3s,3S",
    "pripominka": "pattern=9s,4C2s",
}
SIREN_PATTERN_MIN_VERSION = "00250000"

# Tlacitka se do HA hlasi jako device trigger.  "button" posilaji i senzory,
# jde o jejich parovaci tlacitko.
BUTTON_SUBTYPES = {
    "button": "pairing_button",
    "button1": "button_1",
    "button2": "button_2",
    "button3": "button_3",
    "button4": "button_4",
    "pairingbutton": "pairing_button",
}

# Pripony discovery temat, ktera brana pro jeden uzel vytvari.  Pouzivaji se
# pri odparovani, kdy je nutne retained dokumenty aktivne smazat - jinak by
# zarizeni v Home Assistantu zustalo navzdy.
DISCOVERY_SUFFIXES = (
    ("sensor", "_battery"),
    ("sensor", "_battery_voltage"),
    ("sensor", "_position"),
    ("sensor", "_calibration"),
    ("sensor", "_temperature"),
    ("sensor", "_ver"),
    ("sensor", "_hwver"),
    ("sensor", "_event"),
    ("binary_sensor", "_contact"),
    ("binary_sensor", "_tilt"),
    ("binary_sensor", "_motion"),
    ("binary_sensor", "_siren"),
    ("siren", "_siren"),
    ("select", "_pattern"),
    ("button", "_unpair"),
    ("button", "_calibrate"),
    ("button", "_calibrate_step1"),
    ("button", "_calibrate_step2"),
    ("button", "_cal_reset"),
    ("button", "_siren_off"),
    ("event", "_button"),
)

# Totez pro entity samotne zakladny.  Klic zakladny se meni ve chvili, kdy si
# necha podepsat certifikat - do te doby se pouziva adresa.  Bez uklidu by po
# tom prechodu zustala v Home Assistantu druha, mrtva zakladna.
BASE_DISCOVERY_SUFFIXES = (
    ("sensor", "_id"),
    ("sensor", "_ip"),
    ("sensor", "_uptime"),
    ("binary_sensor", "_alarm"),
    ("select", "_mode"),
    ("button", "_pair_start"),
    ("button", "_pair_stop"),
    ("button", "_reglist"),
    ("button", "_alarm_ack"),
)
BASE_STATE_TOPICS = ("id", "ip", "uptime", "mode", "alarm")

# Stavova temata jednoho uzlu, take retained.
STATE_TOPICS = (
    "raw",
    "battery",
    "position",
    "contact",
    "tilt",
    "calibration",
    "temperature",
    "ver",
    "hwver",
    "last_event",
    "siren",
    "pattern",
)

# Temata, ktera nesou okamzikovou udalost, ne stav.  Pri obnove po startu se
# preskakuji, aby se v HA nespustil pohyb nebo stisk ze stare historie.
MOMENTARY_TOPICS = {"motion", "button", "event"}

# Rezimy alarmu, ktere zakladna hlasi v alarm.*.settings_changed.mode.
# "home" a "away" jsou overene na zivem HW, zbytek pochazi z konfigurace
# a z pravidel v CRE.
ALARM_MODES = {"home", "away", "night", "custom"}

# Tlacitka v Home Assistantu, ktera se vazou na zakladnu jako celek.
BASE_CONTROLS = (
    ("alarm_ack", "Zrusit poplach", "mdi:bell-off"),
    ("pair_start", "Parovani zapnout", "mdi:plus-network"),
    ("pair_stop", "Parovani vypnout", "mdi:network-off"),
    ("reglist", "Vypsat zarizeni", "mdi:format-list-bulleted"),
)

# Tlacitka vazana ke konkretnimu senzoru.  Kalibrace ma smysl jen u typu,
# ktere hlasi polohu.
#
# "unpair" posila prikaz "delete" zakladne a ceka, az ta sama potvrdi
# udalosti "deleted" (viz Gateway.publish_event) - pro zarizeni, jehoz
# zakladna uz neni dostupna (vymenena, offline, mimo tuto instanci), by na
# potvrzeni cekalo navzdy.  "forget" nic neposila zadne zakladne a rovnou
# smaze mistni evidenci i discovery, viz Gateway._forget_device.
DEVICE_CONTROLS = (
    ("unpair", "Odparovat", "mdi:link-off"),
    ("forget", "Zapomenout i bez základny", "mdi:eraser"),
)
POSITION_CONTROLS = (
    ("calibrate", "Kalibrovat", "mdi:tune"),
    ("cal_reset", "Zrusit kalibraci", "mdi:restore"),
)
# Univerzalni senzor si kazdou z obou mezi ulozi zvlast, takze musi byt
# oddelene i tlacitka - mezi kroky uzivatel senzor fyzicky prestavi.
TWO_STEP_CONTROLS = (
    ("calibrate_step1", "Kalibrace 1 - zavreno", "mdi:numeric-1-box"),
    ("calibrate_step2", "Kalibrace 2 - otevreno", "mdi:numeric-2-box"),
    ("cal_reset", "Zrusit kalibraci", "mdi:restore"),
)

# The CRE manifest points at /api/v1/bs01/configuration/{libs,rules}/<group>/<file>.lua
# and the base station downloads every file whose version it does not have yet.
CRE_SOURCE_RE = re.compile(
    r"^/api/v1/bs01/configuration/(?:libs|rules)/[^/]+/(?P<file>[A-Za-z0-9_.-]+\.lua)$"
)

# Message types accepted by the firmware dispatcher in source/services/cloud.c
# (string table of tram bas-002.012.002, offsets 282556..282642).  Anything
# else is discarded with "Message not handled".
CLOUD_MESSAGE_TYPES = {
    "heartbeat",
    "config-set",
    "config-req",
    "endnode",
    "maintenance",
    "configuration-changed",
    "cre-event",
    "rcsh",
}


# Maintenance actions we can run on the base station.  Each entry is a pair
# (kind, builder): "ule" sends a bare JBus command to UleApp, "shell" runs a
# stock script from /usr/bin.  Device ids are validated against ENDNODE_ID_RE
# before interpolation, so no shell metacharacter can reach the command line.
CONTROL_ACTIONS = {
    # "ule" actions are plain JBus commands on the ulecontrol topic; the
    # generated library writes the JSON itself, so no firmware file is needed.
    #
    # The command names MUST be lowercase.  UleApp compares them literally
    # against its own table ("regon", "regoff", "reglist", "deleteall",
    # "delete", "fwupdate", ...).  The stock /usr/bin/regon.json ships
    # {"cmd":"regOn"}, which never matches and is silently forwarded to an
    # end-node instead - a bug in the original firmware, verified against the
    # string table of the decompressed uleapp binary.
    "pair_start": ("pairon", lambda _device_id: "regon"),
    "pair_stop": ("ule", lambda _device_id: "regoff"),
    "reglist": ("ule", lambda _device_id: "reglist"),
    "unpair": ("ule", lambda _device_id: "delete"),
    # Kalibrace okennich/dvernich senzoru.  Tohle jsou prikazy pro KONCOVY UZEL,
    # ne pro UleApp, takze musi jit pres CRE volani ule_command_send.  Zapis na
    # JBus tema ulecontrol nefunguje: UleApp porovnava nazev proti sve tabulce a
    # co v ni neni, zahodi.  Stock /usr/bin/calibrate.sh proto nikdy nic nedelal
    # (overeno v logu - "cal" pres sender nikdy neprineslo odpoved "caldone",
    # zatimco ule_command_send ano, do 4 s).
    #
    # Postup pro vynucenou rekalibraci:
    #   1. cal_reset -> "recal"  zahodi soucasnou kalibraci
    #   2. senzor zacne posilat "calreq"
    #   3. knihovna ws02/ds02 mu automaticky odpovi "cal", cimz se soucasna
    #      poloha ulozi jako "zavreno" (senzor potvrdi udalosti "caldone")
    # Samotny "cal" senzor prijme jen v reakci na vlastni "calreq"; mimo nej ho
    # ignoruje, takze akce "calibrate" ma smysl hlavne pro nezkalibrovany uzel.
    #
    # POZOR: "moving" a "closed" ze tridy SensorCommand v APK jsou nazvy STAVU
    # cloud API, ne ULE prikazy.  Overeno na zivem HW: senzor na ne nijak
    # nereaguje.
    "calibrate": ("endnode", lambda _device_id: "cal"),
    "cal_reset": ("endnode", lambda _device_id: "recal"),
    # Univerzalni senzor um01 kalibruje dvema kroky a sam si o ne rika
    # udalostmi "cal1req" a "cal2req", ale v tomhle rezimu prikaz "cal"
    # ignoruje uplne - firmware ho zahodi uz podle typu senzoru, driv nez by
    # se vubec podival, jestli nejaky "calreq" probihal (viz UM01_FIRMWARE.md).
    # Prvni krok proto neni prikaz kalibrace, ale "cfgclose": ten vyvola na
    # senzoru restart, po kterem si sam ulozi aktualni polohu jako zavrenou.
    # Druhy krok je "cal2", ktery ulozi otevrenou polohu - mezi kroky musi
    # uzivatel senzor fyzicky prestavit.
    "calibrate_step1": ("endnode", lambda _device_id: "cfgclose"),
    "calibrate_step2": ("endnode", lambda _device_id: "cal2"),
    # Libovolny prikaz pro uzel, zadany v souboru s pozadavky polozkou
    # "command".  Slovnik ULE prikazu neni v binarkach zakladny - ta text jen
    # prepolsi - ale pro um01 je vycteny z firmwaru uzlu, viz UM01_FIRMWARE.md.
    "endnode_command": ("endnode", lambda _device_id: ""),
    # Rucni ovladani sireny.  Jde primo na uzel stejnym prikazem, jaky posila
    # firmware v is01.on_unmanaged / off_unmanaged, tedy s obejitim
    # priority_manageru - probihajici poplach muze sirenu vzapeti prepnout zpet.
    "siren_on": ("endnode", lambda _device_id: "sirenon"),
    "siren_off": ("endnode", lambda _device_id: "sirenoff"),
    **{
        f"pattern_{name}": ("endnode", lambda _device_id, cmd=command: cmd)
        for name, command in SIREN_PATTERNS.items()
    },
    # Zruseni probihajiciho poplachu.  internal_rule_intrusion.on_local_event
    # na nej vola ack_intrusion() -> alarm.intrusion.end -> pravidlo vypne
    # sirenu.  Drivejsi pokus zustal bez odezvy proto, ze se local_event_send
    # volal jen s nazvem udalosti; firmware vsude predava jeste jeden argument
    # (doslova "unused_parameter"), bez nej se udalost vubec nerozesle.
    "alarm_ack": ("localevent", lambda _device_id: "alarm.intrusion.ack"),
    # Prepnuti rezimu alarmu.  Kazdy rezim ma vlastni akci, takze se nemusi
    # validovat volny text - mnozina povolenych hodnot je dana uz tim, ktere
    # akce vubec existuji.
    **{
        f"mode_{name}": ("alarmmode", lambda _device_id, mode=name: mode)
        for name in sorted(ALARM_MODES)
    },
}
CONTROL_ACTIONS_NEEDING_DEVICE = {
    "unpair",
    "calibrate",
    "calibrate_step1",
    "calibrate_step2",
    "cal_reset",
    "endnode_command",
    "siren_on",
    "siren_off",
    *(f"pattern_{name}" for name in SIREN_PATTERNS),
}

# Request ids end up inside a Lua string literal, so keep them boring.
CONTROL_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")

# Volny prikaz pro koncovy uzel.  Konci v Lua literalu a pak na DECT lince,
# takze projde jen to, co vypada jako "cal", "set=hbtime,60" nebo "pattern=2h,2D".
ENDNODE_COMMAND_RE = re.compile(r"[a-z0-9=,;._-]{1,48}")

# Hlaseni z Lua knihoven zakladny.  Zadny jiny pristup k jejich logu neexistuje
# (seriova konzole je ve firmwaru vypnuta), takze se vypisuji na stdout.
CRE_DIAG_RE = re.compile(r"^/api/v1/diag/cre/(?P<level>[a-z]+)")

# Hard limit for a single command.  BusyBox 1.24.2 spells this "-t SECS".
# Without it a hanging script would block the Lua VM until the watchdog fires.
CONTROL_COMMAND_TIMEOUT = 10

# Jak casto se gwctl pta brany na nove prikazy.  Cim kratsi perioda, tim rychlejsi
# odezva, ale kazdy dotaz je os.execute (wget), ktery na dobu spojeni blokuje
# Lua VM.  Pet sekund je kompromis: pri dostupne brane trva dotaz par desitek ms.
CONTROL_POLL_INTERVAL = 5

# Prosty HTTP port, na kterem si gwctl vyzvedava prikazy.  Musi byt bez TLS,
# busybox wget na zakladne HTTPS neumi.
CONTROL_POLL_PORT = 8080

# Sitovy timeout wgetu a tvrdy strop celeho dotazu.  Kdyz brana nebezi, nesmi
# se cekani protahnout - Lua VM je po tu dobu zablokovana.
CONTROL_POLL_TIMEOUT = 2
CONTROL_POLL_HARD_TIMEOUT = 4

# Parovaci okno DECT se samo nezavira - firmware ho drzi otevrene, dokud
# nedorazi "regoff".  Casovac bezi primo na zakladne, takze okno zavre i tehdy,
# kdyz brana mezitim spadne.
CONTROL_PAIR_TIMEOUT = 600

CONTROL_LUA_TEMPLATE = r"""-- Generovano gigaset_gateway.py, needitovat rucne.
-- POZOR: nic se NESMI spoustet pri nacteni modulu.  os.execute blokuje celou
-- Lua VM; kdyz to potka bootovaci cestu, watchdog zakladnu zresetuje a vznikne
-- smycka, ze ktere uz neni cesta ven po siti (jen pres UART).
--
-- Tento modul se uz kvuli jednotlivym prikazum negeneruje znovu.  Zakladna po
-- kazde zmene knihovny provadi kompletni reload CRE, ktery trva 16-26 s, a to
-- byla drtiva vetsina odezvy.  Misto toho zustava modul trvale nahrany a sam
-- se v casovaci pta brany, jestli neco nema udelat.
local M = {}
local URL = "__URL__"
local HOST = "__HOST__"
local PORT = "__PORT__"
local INTERVAL = __INTERVAL__
local POLL = "/tmp/gwctl.poll"
local OUT = "/tmp/gwctl.out"
local ULE = "/tmp/gwctl.ule.json"
local SEND = "/tmp/gwctl.send.sh"
local LIST = "/tmp/gwctl.list"
local REQ = "/tmp/gwctl.req"
local ARM = "/tmp/gwarm."
local POLL_TIMER = "gwctl_poll"
local PAIR_TIMER = "gwctl_pairoff"

-- Popis CRE teto zakladny se posle brane jednou za nacteni modulu.  Jinak by ho
-- uzivatel musel vycist z flash, protoze zadna jina cesta k nemu nevede.
local state_sent = false

-- V zavadecim rezimu neni cloudLog v manifestu, takze modul nemusi existovat.
-- Bez teto pojistky by na nem kazde hlaseni skoncilo chybou, kterou pcall
-- spolkne - a s ni i praci, ktera se mela udelat.
local function log(...)
    if type(cloudLog) == "table" and cloudLog.warn ~= nil then
        cloudLog.warn(...)
    end
end

local function shell(label, cmd)
    os.execute("( timeout -t __TIMEOUT__ " .. cmd .. " ) > " .. OUT .. " 2>&1")
    local handle = io.open(OUT, "r")
    local output = ""
    if handle ~= nil then
        output = (handle:read("*a") or ""):gsub("%s+", " ")
        handle:close()
    end
    log("gwctl {} [{}]", label, output:sub(1, 300))
end

-- Manifest CRE zakladny (/mnt/data/cfg/cre) jmenuje presne ty Lua, ktere ma
-- nacist, a na kazde zakladne je jiny.  Brana ho jinak nema odkud vzit - do
-- flash se uzivatel bez rozebrani krabicky nedostane.
--
-- Vedle nej jde i vypis /mnt/data/cre.  Manifest je jen seznam jmen, takze se z
-- vypisu da slozit i tehdy, kdyz uz zakladna svuj manifest prepsala.
--
-- Cely HTTP pozadavek se sklada tady v Lua a shell uz jen preleje soubor do nc.
-- Busybox na zakladne nema printf ani wget s dlouhymi volbami, takze hlavicku
-- neni cim napsat a POST neni cim poslat.
local function post(path, source)
    local input = io.open(source, "r")
    if input == nil then
        return
    end
    local body = input:read("*a") or ""
    input:close()
    if body == "" then
        return
    end
    local request = io.open(REQ, "w")
    if request == nil then
        return
    end
    request:write("POST ", path, " HTTP/1.0\r\n")
    request:write("Content-Length: ", tostring(#body), "\r\n\r\n")
    request:write(body)
    request:close()

    local script = io.open(SEND, "w")
    if script == nil then
        return
    end
    script:write("cat " .. REQ .. " | nc " .. HOST .. " " .. PORT .. "\n")
    script:close()
    shell("odeslani " .. path, "/bin/sh " .. SEND)
end

local function send_state()
    if state_sent or HOST == "" then
        return
    end
    state_sent = true
    local script = io.open(SEND, "w")
    if script == nil then
        return
    end
    -- Dlouhy retezec, aby se nemuselo escapovat nic z toho, co potrebuje shell.
    script:write([==[
for d in endnode_libraries rules internal_rules; do
  for f in /mnt/data/cre/$d/*.lua; do
    [ -e "$f" ] && echo "$d/${f##*/}"
  done
done > /tmp/gwctl.list
]==])
    script:close()
    shell("vypis", "/bin/sh " .. SEND)
    post("/inventory", LIST)
    post("/manifest", "/mnt/data/cfg/cre")
end

-- Prikaz pro UleApp na JBus tematu ulecontrol.  "sender" umi vzit payload ze
-- souboru, takze si ho nejdriv sami napiseme a nemusime resit shell quoting.
-- Nazvy prikazu jsou vzdy malymi pismeny (regon/regoff/reglist/delete/...).
local function ule(label, cmd, dev)
    local handle = io.open(ULE, "w")
    if handle == nil then
        log("gwctl {} [nelze zapsat {}]", label, ULE)
        return
    end
    if dev ~= nil and dev ~= "" then
        handle:write('{"cmd":"' .. cmd .. '","devId":"' .. dev .. '"}')
    else
        handle:write('{"cmd":"' .. cmd .. '"}')
    end
    handle:close()
    shell(label, "/usr/bin/sender 127.0.0.1 ulecontrol " .. ULE)
end

-- Parovaci okno se samo nezavira, takze se k nemu rovnou nastavi casovac.
-- Bezi na zakladne, tedy plati i kdyz brana mezitim spadne nebo se restartuje.
--
-- Pred "regon" se vzdy posle "regoff", i kdyz okno podle nas jeste bezi.
-- Duvod: opakovane "Parovani zapnout" behem jiz otevreneho okna nekdy nevedlo
-- k uspesnemu sparovani, zatimco pockat na prirozene vyprseni (ktere take
-- konci "regoff") a pak spustit parovani znovu spolehlive pomohlo. Nejde tedy
-- o zavrene okno (regon zustava stejny), ale nejspis o to, ze UleApp bere
-- "regon" behem jiz aktivniho parovani jako no-op a vnitrni stav si neresetuje
-- - explicitni "regoff" tesne pred kazdym "regon" tomu ma zabranit.
local function pairon(label, cmd, dev, devtype)
    ule(label, "regoff", "")
    ule(label, cmd, dev)
    timer_set(M.name, PAIR_TIMER, __PAIR_TIMEOUT__, "s")
    log("gwctl parovaci okno se zavre za {} s", __PAIR_TIMEOUT__)
end

-- Prikaz pro konkretni koncovy uzel (senzor).  Musi jit pres CRE volani,
-- protoze UleApp propousti jen prikazy ze sve vlastni tabulky.
--
-- ULE senzor vetsinu casu spi, takze prikaz odeslany prave ted se k nemu
-- nemusi dostat.  Vedle okamziteho pokusu proto polozime znacku do /tmp;
-- knihovna daneho typu ji odesle znovu ve chvili, kdy se uzel sam ozve, a pak
-- ji smaze.
local function endnode(label, cmd, dev, devtype)
    log("gwctl {} ule_command_send {} {} {}", label, devtype, dev, cmd)
    local handle = io.open(ARM .. dev, "w")
    if handle ~= nil then
        handle:write(cmd)
        handle:close()
    end
    ule_command_send(devtype, dev, cmd)
end

-- Prepnuti rezimu alarmu.  Stejnou cestou to dela i pravidlo navazane na
-- tlacitko bn01, takze zakladna zmenu potvrdi udalosti
-- alarm.*.settings_changed.mode a vsichni ostatni ji uvidi.
local function alarmmode(label, mode, dev, devtype)
    log("gwctl {} rezim alarmu {}", label, mode)
    local_event_send("alarm.*.settings_changed.mode", mode)
end

-- Lokalni CRE udalost bez smysluplneho parametru, napriklad zruseni
-- probihajiciho poplachu.  Argument se predava vzdy - volani jen s nazvem
-- udalosti zustane bez odezvy, proto ho firmware vsude vyplnuje konstantou.
local function localevent(label, event, dev, devtype)
    log("gwctl {} local_event_send {}", label, event)
    local_event_send(event, "unused_parameter")
end

local HANDLERS = {
    ule = ule,
    pairon = pairon,
    endnode = endnode,
    alarmmode = alarmmode,
    localevent = localevent,
}

local function split(line)
    local parts = {}
    local start = 1
    while true do
        local pos = string.find(line, "|", start, true)
        if pos == nil then
            parts[#parts + 1] = string.sub(line, start)
            return parts
        end
        parts[#parts + 1] = string.sub(line, start, pos - 1)
        start = pos + 1
    end
end

-- Radek z brany ma tvar druh|popis|argument|devId|devType.
local function poll()
    os.remove(POLL)
    os.execute(
        "( timeout -t __POLL_HARD__ /usr/bin/wget -q -T __POLL_TIMEOUT__ -O "
            .. POLL .. " " .. URL .. " ) > /dev/null 2>&1"
    )
    local handle = io.open(POLL, "r")
    if handle == nil then
        return
    end
    for line in handle:lines() do
        local parts = split(line)
        local handler = HANDLERS[parts[1]]
        if handler ~= nil then
            pcall(handler, parts[2] or "", parts[3] or "", parts[4] or "", parts[5] or "")
        end
    end
    handle:close()
    os.remove(POLL)
end

function M.on_start(module_name)
    M.name = M.name or module_name
    -- Samotny timer_set je levny, dotaz na branu probehne az v on_timer.
    timer_set(M.name, POLL_TIMER, INTERVAL, "s")
end

function M.on_timer(command)
    if command == PAIR_TIMER then
        ule("auto-pairoff", "regoff", "")
        return
    end
    if command ~= POLL_TIMER then
        return
    end
    -- Prodlouzit smycku drive nez se cokoliv udela; kdyby dotaz nebo prikaz
    -- selhal, dalsi kolo se stejne naplanuje.
    timer_set(M.name, POLL_TIMER, INTERVAL, "s")
    pcall(send_state)
    pcall(poll)
end

return M
"""


def control_lua_source(
    url: str,
    interval: int = CONTROL_POLL_INTERVAL,
    timeout: int = CONTROL_COMMAND_TIMEOUT,
    pair_timeout: int = CONTROL_PAIR_TIMEOUT,
) -> str:
    """Render the permanently loaded CRE library that polls the gateway."""
    authority = url.split("//", 1)[-1].split("/", 1)[0]
    host, _, port = authority.partition(":")
    return (
        CONTROL_LUA_TEMPLATE.replace("__URL__", url)
        .replace("__HOST__", host)
        .replace("__PORT__", port or "80")
        .replace("__INTERVAL__", str(interval))
        .replace("__TIMEOUT__", str(timeout))
        .replace("__POLL_TIMEOUT__", str(CONTROL_POLL_TIMEOUT))
        .replace("__POLL_HARD__", str(CONTROL_POLL_HARD_TIMEOUT))
        .replace("__PAIR_TIMEOUT__", str(pair_timeout))
    )


def control_poll_line(request: dict[str, str]) -> str:
    """Serialise one control request into the line format gwctl parses."""
    kind, build = CONTROL_ACTIONS[request["action"]]
    device_id = request.get("device_id", "")
    device_type = request.get("device_type", "")
    label = f"{request['id']}/{request['action']}"
    return "|".join(
        (kind, label, request.get("command") or build(device_id), device_id, device_type)
    )


def temperature_from_payload(sink: str, payload: str) -> float | None:
    """Teplota ve stupnich Celsia, nebo None, kdyz hodnota teplotou neni.

    Uzel ji hlasi ve trech tvarech: pod "tp" jako prvni polozku seznamu
    oddeleneho carkami (druha polozka je tlak, viz volani nize), pod "state"
    jako druhou ze ctyr polozek oddelenych strednikem - obe v desetinach
    stupne - a pod "temp" jako dve hodnoty oddelene lomitkem, uz ve stupnich.
    """
    if sink == "temp":
        try:
            return float(payload.split("/")[0])
        except ValueError:
            return None
    if sink == "tp":
        field = payload.split(",")[0]
    else:
        parts = payload.split(";")
        if len(parts) != 4:
            return None
        field = parts[1]
    try:
        return int(field.strip().replace(".", "")) / 10
    except ValueError:
        return None


def pressure_from_payload(payload: str) -> float | None:
    """Tlak v hektopascalech, nebo None, kdyz hodnota tlakem neni."""
    try:
        return float(payload)
    except ValueError:
        return None


def cloud_event_for_command(command: dict[str, Any]) -> dict[str, Any]:
    """Build an incoming cloud message for the base station."""
    kind = command.get("type")
    if kind == "endnode":
        # The CRE endnode libraries (cre/endnode_libraries/*.lua) receive this
        # as M.on_cloud_event("device.command", data) and forward it to
        # ule_command_send(deviceType, deviceId, command).
        return {
            "type": "cre-event",
            "event": "device.command",
            "data": {
                "deviceType": command["device_type"],
                "deviceId": command["device_id"],
                "command": command["command"],
                "priority": 0,
            },
        }
    if kind == "raw":
        return dict(command["message"])
    raise ValueError(f"Unsupported event command type: {kind}")


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
    temporary.replace(path)


def cre_source(config: dict[str, Any], filename: str) -> bytes | None:
    """Return the Lua source for a file referenced by the CRE manifest.

    Directories are searched in order, so a file dropped into the first entry
    overrides the copy extracted from the base station flash.
    """
    # ``filename`` already matched CRE_SOURCE_RE, so it cannot contain a path
    # separator or ".." and can never escape the configured directories.
    for directory in config.get("cre_source_dirs", []):
        candidate = Path(directory) / filename
        if candidate.is_file():
            return candidate.read_bytes()
    return None


# Skupiny CRE souboru tak, jak lezi na zakladne v /mnt/data/cre.
CRE_GROUPS = ("endnode_libraries", "rules", "internal_rules")
INVENTORY_RE = re.compile(
    r"^(?P<group>[a-z_]+)/(?P<file>(?P<key>[A-Za-z0-9_.-]+)-(?P<version>[0-9]+)\.lua)$"
)


def manifest_from_inventory(listing: str) -> dict[str, Any]:
    """Slozit CRE manifest z vypisu souboru, ktere zakladna ma na disku.

    Manifest neni nic jineho nez seznam jmen, takze se da rekonstruovat i tehdy,
    kdyz uz zakladna svuj puvodni /cfg/cre neme - a to je jedina cesta, jak ho
    ziskat bez vycteni flash pameti.  Ze jmena souboru plyne i cislo skupiny:
    custom_template_rule_recipe-30599010-143.lua patri do skupiny 30599010.
    """
    best: dict[str, dict[str, tuple[int, str]]] = {group: {} for group in CRE_GROUPS}
    for line in listing.splitlines():
        match = INVENTORY_RE.match(line.strip())
        if match is None or match.group("group") not in best:
            continue
        group = match.group("group")
        key = match.group("key")
        version = int(match.group("version"))
        if group == "endnode_libraries":
            url = f"/api/v1/bs01/configuration/libs/{key}/{match.group('file')}"
        else:
            identifier = key.rsplit("-", 1)[-1]
            if not identifier.isdigit():
                continue
            url = f"/api/v1/bs01/configuration/rules/{identifier}/{match.group('file')}"
        # Zakladna si stare verze nechava, takze plati ta nejvyssi.
        if key not in best[group] or best[group][key][0] < version:
            best[group][key] = (version, url)
    return {
        group: {key: url for key, (_, url) in sorted(entries.items())}
        for group, entries in best.items()
    }


def local_configuration(
    config: dict[str, Any], manifest_path: Path
) -> tuple[dict[str, Any], str]:
    # POZOR: zakladna soubory, ktere v manifestu nejsou, ze sveho disku smaze -
    # vcetne pravidel, ktera uz z vypnuteho cloudu nikdo neziska. Proto se
    # manifest_path posila cely a beze zmeny, presne jak ho uzivatel dodal pro
    # tuhle konkretni zakladnu (viz cre_manifest_map).
    cre_manifest = load_config(manifest_path)
    base_configuration = load_config(Path(config["base_configuration_file"]))
    canonical = json.dumps(
        {"cre": cre_manifest, "configuration": base_configuration},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    cre_uid = config.get("cre_uid")
    if cre_uid is None:
        cre_uid = "local-" + hashlib.sha256(canonical).hexdigest()[:24]
    if not isinstance(cre_uid, str) or not cre_uid:
        raise ValueError("cre_uid must be a non-empty string")
    return {
        "cre_uid": cre_uid,
        "cre": cre_manifest,
        "configuration": base_configuration,
    }, cre_uid


def is_deleted_device(device: dict[str, Any]) -> bool:
    """Byl uzel odparovan?

    Ve stavu se drzi posledni zprava z kazdeho sinku, takze udalost "deleted"
    tam zustane vedle starsich hodnot jako ver nebo ba.  Pri obnove po startu
    by se z nich entita v Home Assistantu znovu vytvorila.
    """
    return any(
        isinstance(item, dict) and item.get("payload") == "deleted"
        for item in device.values()
    )


def base_key(peer: str) -> str:
    """Klic zakladny pro MQTT tema a identifikator zarizeni.

    Odvozuje se z adresy, protoze to je jedina hodnota, na ktere se shodne
    kazda instance brany - vlastni ID rekne zakladna jen v zadosti o podpis
    certifikatu, a tu posle jednou za nekolik let.  Proto je nutne mit pro ni
    v DHCP pevnou adresu.
    """
    return peer.replace(".", "_").replace(":", "_")


def base_identity_from_action(path: str, body: str) -> str:
    """ID zakladny z hlaseni o vykonanem pravidle.

    Zadost o podpis certifikatu chodi jen jednou za nekolik let, takze nove
    spustena instance brany by se ID jinak nedozvedela a zalozila by v Home
    Assistantu druhou zakladnu vedle te puvodni.
    """
    if not path.startswith("/api/v1/bs01/sink/action"):
        return ""
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        return ""
    if not isinstance(document, dict):
        return ""
    rule = document.get("rule")
    system = rule.get("system") if isinstance(rule, dict) else None
    if not isinstance(system, dict):
        return ""
    identity = str(system.get("device_id", "")).strip().lower()
    return identity if BASE_IDENTITY_RE.fullmatch(identity) else ""


def normalize_control_request(item: dict[str, Any]) -> dict[str, str]:
    """Zkontrolovat a znormalizovat jeden ridici pozadavek.

    Hodnoty koncí v Lua zdrojaku jako holé literály, takze se tady musi
    zkontrolovat uplne vsechno, co odtud muze projit dal.
    """
    request_id = str(item.get("id", ""))
    action = str(item.get("action", ""))
    device_id = str(item.get("device_id", "")).lower()
    device_type = str(item.get("device_type", "")).lower()
    if CONTROL_ID_RE.fullmatch(request_id) is None:
        raise ValueError(f"Control request id is not safe: {request_id!r}")
    if action not in CONTROL_ACTIONS:
        raise ValueError(f"Unknown control action: {action}")
    if action in CONTROL_ACTIONS_NEEDING_DEVICE:
        if ENDNODE_ID_RE.fullmatch(device_id) is None:
            raise ValueError(f"Control request {request_id} has invalid device_id")
    else:
        device_id = ""
    if CONTROL_ACTIONS[action][0] == "endnode":
        # ule_command_send needs the device type as its first argument.
        if ENDNODE_TYPE_RE.fullmatch(device_type) is None:
            raise ValueError(f"Control request {request_id} needs a valid device_type")
    else:
        device_type = ""
    command = str(item.get("command", "")).strip().lower()
    if action == "endnode_command":
        if ENDNODE_COMMAND_RE.fullmatch(command) is None:
            raise ValueError(f"Control request {request_id} has invalid command")
    else:
        command = ""
    return {
        "id": request_id,
        "action": action,
        "device_id": device_id,
        "device_type": device_type,
        "command": command,
    }


class MqttBridge:
    def __init__(
        self,
        config: dict[str, Any],
        command_handler: Callable[[str, str, str, str], None] | None = None,
    ) -> None:
        self.config = config
        self.client = None
        self.command_handler = command_handler
        self.base_topic = config.get("base_topic", "gigaset")
        self.discovery_prefix = config.get("discovery_prefix", "homeassistant")
        # Discovery dokumenty jsou retained, staci je poslat jednou.
        self.announced: set[str] = set()
        # Zakladny, kterym uz byl vystaven vychozi rezim alarmu.
        self.seeded_modes: set[str] = set()
        self.replaying = False
        if not config.get("enabled", False):
            return

        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise RuntimeError(
                "MQTT je zapnuté, ale chybí balíček paho-mqtt. "
                "Spusťte: python -m pip install paho-mqtt"
            ) from exc

        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=config.get("client_id", "gigaset-elements-gateway"),
            )
        except (AttributeError, TypeError):
            client = mqtt.Client(client_id=config.get("client_id", "gigaset-elements-gateway"))

        username = config.get("username")
        if username:
            client.username_pw_set(username, config.get("password"))
        if config.get("tls", False):
            client.tls_set()
        client.will_set(f"{self.base_topic}/availability", "offline", retain=True)
        client.on_message = self._on_message
        client.on_connect = self._on_connect
        client.connect(config["host"], int(config.get("port", 1883)), 60)
        client.loop_start()
        self.client = client

    def _on_connect(self, client, _userdata, _flags, _reason, _properties=None) -> None:
        """Obnovit odbery a dostupnost po kazdem pripojeni.

        Kdyz se spojeni s brokerem prerusi, paho se pripoji znovu - ale broker
        mezitim uz rozeslal posledni vuli "offline" a odbery zapomnel.  Bez
        tohoto by brana dal bezela, jen by v Home Assistantu vsechny entity
        zustaly nedostupne a tlacitka by prestala fungovat.
        """
        client.subscribe(f"{self.base_topic}/base/+/control/+")
        client.subscribe(f"{self.base_topic}/base/+/mode/set")
        client.subscribe(f"{self.base_topic}/+/+/command")
        client.publish(f"{self.base_topic}/availability", "online", retain=True)

    def _on_message(self, _client, _userdata, message) -> None:
        """Prevest stisk tlacitka v HA na pozadavek do ridici fronty.

        Vyjimka v teto funkci by umlcela cele MQTT vlakno, proto se chyby jen
        logují.
        """
        if self.command_handler is None:
            return
        if message.retain:
            # Retained zprava na prikazovem tematu je stary prikaz, ktery broker
            # doruci pri kazdem prihlaseni k odberu.  Provest ho znovu by po
            # restartu brany vratilo rezim alarmu na hodnotu, kterou uzivatel
            # davno zmenil.
            print(f"MQTT COMMAND ignorováno retained {message.topic}", flush=True)
            return
        parts = message.topic.split("/")
        try:
            if len(parts) == 5 and parts[1] == "base" and parts[3] == "control":
                self.command_handler(parts[4], "", "", parts[2])
            elif len(parts) == 5 and parts[1] == "base" and parts[3:5] == ["mode", "set"]:
                mode = message.payload.decode("utf-8", "replace").strip().lower()
                # Doruceni prikazu trva desitky sekund.  Stav se proto nastavi
                # rovnou, jinak by select v HA skocil zpatky a uzivatel by mel
                # dojem, ze se nic nestalo.  Potvrzeni ze zakladny hodnotu
                # nasledne prepise - i kdyby zmena neprosla.
                if mode in ALARM_MODES:
                    self.publish(f"{self.base_topic}/base/{parts[2]}/mode", mode)
                self.command_handler(f"mode_{mode}", "", "", parts[2])
            elif len(parts) == 4 and parts[3] == "command":
                action = message.payload.decode("utf-8", "replace").strip()
                if action.startswith("{"):
                    # Entita siren v HA posila {"state": "..."}, ne holy payload.
                    action = str(json.loads(action).get("state", ""))
                if action.startswith("pattern_"):
                    # Uzel prehravany vzor nehlasi, jinak by select v HA zustal
                    # prazdny.
                    self.publish(
                        f"{parts[0]}/{parts[1]}/{parts[2]}/pattern",
                        action[len("pattern_") :],
                    )
                # Tohle tema neni vazane na zakladnu (jen typ/id zarizeni), takze
                # se cilova zakladna dohleda az podle device_base.
                self.command_handler(action, parts[1], parts[2], "")
        except Exception as error:  # noqa: BLE001
            print(f"MQTT COMMAND chyba: {error}", flush=True)

    def announce_base(
        self, base_key: str, initial_mode: str = "", identity: str = ""
    ) -> None:
        """Vystavit ovladaci tlacitka pro zakladnu, ktera se prave ozvala.

        Zamerne se to nedela pri startu: dokud zadna zakladna neexistuje, nema
        smysl v Home Assistantu nabizet parovani.  Klic je soucasti temat i
        identifikatoru, takze si dve zakladny nesahnou na stejne entity.

        Pridani a odebrani samotne zakladny sem nepatri - to musi resit
        integrace, ktera o brane vi driv, nez nejaka zakladna existuje.
        """
        if self.client is None:
            return
        common = self._base_common(base_key)
        for action, name, icon in BASE_CONTROLS:
            self.discovery(
                "button",
                f"gigaset_base_{base_key}_{action}",
                {
                    **self._stateless(common),
                    "name": name,
                    "icon": icon,
                    "command_topic": (
                        f"{self.base_topic}/base/{base_key}/control/{action}"
                    ),
                    "payload_press": action,
                    "unique_id": f"gigaset_base_{base_key}_{action}",
                },
            )
        if initial_mode and base_key not in self.seeded_modes:
            # Rezim se hlasi jen pri zmene, takze bez tohoto by entita do prvniho
            # prepnuti vubec neexistovala.  Vychozi hodnota je ta, kterou
            # zakladne servirujeme v konfiguraci.
            #
            # Poslat se smi jen jednou: announce_base bezi nad KAZDYM pozadavkem
            # zakladny, takze opakovany zapis posledniho potvrzeneho rezimu
            # prebil vyber uzivatele driv, nez zakladna zmenu stihla potvrdit -
            # select v HA skocil zpatky na puvodni hodnotu.
            self.seeded_modes.add(base_key)
            self.publish(f"{self.base_topic}/base/{base_key}/mode", initial_mode)

        # Identifikator zakladny z jejiho certifikatu.
        self.publish(f"{self.base_topic}/base/{base_key}/id", identity or base_key)
        self._base_sensor(
            base_key,
            "id",
            "Base identifier",
            {"icon": "mdi:identifier", "entity_category": "diagnostic"},
        )

        # Discovery se posila hned, aby entity existovaly i pred prvni zpravou -
        # rezim se hlasi az pri zmene.
        # Uptime zakladny se drive vystavoval jako senzor; discovery je retained,
        # takze se stara entita musi aktivne smazat.
        self.remove_discovery("sensor", f"gigaset_base_{base_key}_uptime")
        self.publish(f"{self.base_topic}/base/{base_key}/uptime", "")
        self._base_sensor(
            base_key,
            "alarm",
            "Alarm",
            {"device_class": "safety", "payload_on": "ON", "payload_off": "OFF"},
            component="binary_sensor",
        )
        self.discovery(
            "select",
            f"gigaset_base_{base_key}_mode",
            {
                **self._base_common(base_key),
                "name": "Alarm mode",
                "icon": "mdi:shield-home",
                "state_topic": f"{self.base_topic}/base/{base_key}/mode",
                "command_topic": f"{self.base_topic}/base/{base_key}/mode/set",
                "options": sorted(ALARM_MODES),
                "unique_id": f"gigaset_base_{base_key}_mode",
            },
        )

    def _device_controls(
        self, root: str, object_id: str, common: dict[str, Any], type_code: str
    ) -> None:
        controls = DEVICE_CONTROLS
        if type_code == "um01":
            controls = TWO_STEP_CONTROLS + DEVICE_CONTROLS
        elif type_code in POSITION_TYPES:
            controls = POSITION_CONTROLS + DEVICE_CONTROLS
        for action, name, icon in controls:
            self.discovery(
                "button",
                f"{object_id}_{action}",
                {
                    **self._stateless(common),
                    "name": name,
                    "icon": icon,
                    "entity_category": "config",
                    "command_topic": f"{root}/command",
                    "payload_press": action,
                    "unique_id": f"{object_id}_{action}",
                },
            )

    def publish(self, topic: str, payload: str, retain: bool = True) -> None:
        if self.client is None:
            return
        if self.replaying and topic.rsplit("/", 1)[-1] in MOMENTARY_TOPICS:
            # Pri obnove po startu se posilaji ulozene stavy, ale okamzikove
            # udalosti ne - jinak by se v HA spustil pohyb a stisky tlacitek
            # ze stare historie.
            return
        self.client.publish(topic, payload, retain=retain)

    def announce_known(self, events: list[dict[str, Any]], base: str) -> None:
        """Obnovit discovery i posledni znamy stav zarizeni, ktera brana zna.

        Bez toho by se dokument prepsal az pri prvni udalosti od daneho uzlu a
        entita by do te doby zustala bez hodnoty - u senzoru, ktery se ozve
        jednou za hodinu, je to nepouzitelne.
        """
        self.replaying = True
        try:
            for event in events:
                self.publish_event(event, base)
        finally:
            self.replaying = False

    def discovery(self, component: str, object_id: str, payload: dict[str, Any]) -> None:
        """Publish a Home Assistant discovery document exactly once.

        The documents are retained, so re-sending them on every event would
        only add broker traffic.
        """
        topic = f"{self.discovery_prefix}/{component}/{object_id}/config"
        if topic in self.announced:
            return
        self.announced.add(topic)
        self.publish(topic, json.dumps(payload, separators=(",", ":")), retain=True)

    def remove_discovery(self, component: str, object_id: str) -> None:
        """Smazat retained discovery dokument, ktery uz nema mit protejsek."""
        topic = f"{self.discovery_prefix}/{component}/{object_id}/config"
        if topic in self.announced:
            return
        self.announced.add(topic)
        self.publish(topic, "", retain=True)

    @staticmethod
    def device(type_code: str, device_id: str, base: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "identifiers": [f"gigaset_{type_code}_{device_id}"],
            "manufacturer": "Gigaset",
            "model": type_code,
            "name": f"{DEVICE_NAMES.get(type_code, type_code)} {device_id}",
        }
        if base:
            # Senzory nemluvi do site samy, chodi vzdy pres zakladnu - v Home
            # Assistantu se proto zobrazi jako jeji podrizena zarizeni.
            payload["via_device"] = f"gigaset_base_{base}"
        return payload

    def _base_common(self, base_key_value: str) -> dict[str, Any]:
        return {
            "availability_topic": f"{self.base_topic}/availability",
            "device": {
                "identifiers": [f"gigaset_base_{base_key_value}"],
                "manufacturer": "Gigaset",
                "model": "bs01",
                "name": f"Gigaset zakladna {base_key_value}",
            },
        }

    def _base_sensor(
        self,
        base_key_value: str,
        slug: str,
        name: str,
        extra: dict[str, Any] | None = None,
        component: str = "sensor",
    ) -> None:
        object_id = f"gigaset_base_{base_key_value}"
        self.discovery(
            component,
            f"{object_id}_{slug}",
            {
                **self._base_common(base_key_value),
                "name": name,
                "state_topic": f"{self.base_topic}/base/{base_key_value}/{slug}",
                "unique_id": f"{object_id}_{slug}",
                **(extra or {}),
            },
        )

    def publish_base_telemetry(
        self, base_key_value: str, peer: str, path: str, body: str
    ) -> str:
        """Vystavit to, co zakladna hlasi sama o sobe.

        Adresa, doba behu, rezim alarmu a probihajici poplach.  Vraci nove
        potvrzeny rezim, aby si ho brana mohla zapamatovat - jinak by se po
        restartu prepsal vychozi hodnotou z konfigurace.
        """
        if self.client is None:
            return ""
        root = f"{self.base_topic}/base/{base_key_value}"

        self.publish(f"{root}/ip", peer)
        self._base_sensor(
            base_key_value,
            "ip",
            "IP address",
            {"icon": "mdi:ip-network", "entity_category": "diagnostic"},
        )

        if not path.startswith("/api/v1/bs01/sink"):
            return ""
        try:
            document = json.loads(body)
        except json.JSONDecodeError:
            return ""
        if not isinstance(document, dict):
            return ""

        if document.get("type") == "operation.uptime.status":
            return ""

        action = str(document.get("action_type", ""))
        # Zakladna potvrzuje zmenu jak udalosti "...settings_changed.mode", tak
        # "...settings_changed.mode.confirmation".  Kdyz se chytala jen prvni,
        # zustal stav v HA na stare hodnote - a protoze select neposila prikaz
        # pri vyberu uz nastavene volby, nesel rezim vratit zpatky.
        if "settings_changed.mode" in action:
            mode = str(document.get("new_mode", "")).lower()
            if mode in ALARM_MODES:
                self.publish(f"{root}/mode", mode)
                # Firmware pri prechodu do jineho rezimu nez "away" potvrzuje
                # probihajici poplach (internal_rule_intrusion: handle_mode_change
                # vola ack_intrusion).  Vlastni udalost o konci uz neposle, takze
                # bez tohoto by stav poplachu zustal viset na ON - a kdyz brana
                # zrovna nebezela, uz by ho nic nesundalo.
                if mode != "away":
                    self.publish(f"{root}/alarm", "OFF")
                return mode
            return ""

        if action.startswith("alarm.") and action.endswith(".start"):
            self.publish(f"{root}/alarm", "ON")
            return ""

        if action.startswith("alarm.") and action.endswith((".end", ".stop", ".ack")):
            self.publish(f"{root}/alarm", "OFF")
            return ""

        return ""

    def _common(self, type_code: str, device_id: str, base: str = "") -> dict[str, Any]:
        return {
            "availability_topic": f"{self.base_topic}/availability",
            "device": self.device(type_code, device_id, base),
        }

    @staticmethod
    def _stateless(common: dict[str, Any]) -> dict[str, Any]:
        """Discovery bez dostupnosti - pro tlacitka a event entity.

        Jejich stav v HA je jen cas posledniho stisku.  Pri restartu brany se
        temata dostupnosti prehoupnou pres "offline", HA po navratu ulozeny
        cas obnovi a zapise ho do logu jako novy stisk.  Bez dostupnosti tyhle
        entity zustanou navzdy dostupne a log zustane cisty; stisk se stejne
        neztrati, protoze prikaz putuje samostatnym tematem.
        """
        return {
            key: value for key, value in common.items() if key != "availability_topic"
        }

    def _battery(self, root: str, object_id: str, common: dict[str, Any], payload: str) -> None:
        """Rozlozit trojici napeti ze sink/ba.

        Vyznam hodnot neni v firmwaru nikde popsany, ale z historie je videt, ze
        prvni je vzdy nejnizsi a nejvic kolisa - odpovida napeti pod zatezi pri
        vysilani.  Druha je temer konstantni (klidove napeti).  Jako ukazatel
        stavu se proto bere prvni hodnota.

        Procenta jsou odhad z rozsahu dvou clanku AA/AAA v serii; presnou
        krivku neznáme, proto jde rozsah nastavit v konfiguraci.
        """
        values: list[int] = []
        try:
            values = [int(part) for part in payload.split(",")]
        except ValueError:
            pass
        if not values:
            return

        millivolts = values[0]
        empty = int(self.config.get("battery_empty_mv", 2200))
        full = int(self.config.get("battery_full_mv", 3100))
        percent = round((millivolts - empty) / max(full - empty, 1) * 100)
        percent = max(0, min(100, percent))

        self.publish(
            f"{root}/battery",
            json.dumps(
                {
                    "percent": percent,
                    "millivolts": millivolts,
                    "values_mv": values,
                    "raw": payload,
                },
                separators=(",", ":"),
            ),
        )
        self.discovery(
            "sensor",
            object_id + "_battery",
            {
                **common,
                "name": "Battery",
                "device_class": "battery",
                "state_class": "measurement",
                "unit_of_measurement": "%",
                "state_topic": f"{root}/battery",
                "value_template": "{{ value_json.percent }}",
                "json_attributes_topic": f"{root}/battery",
                "entity_category": "diagnostic",
                "unique_id": object_id + "_battery",
            },
        )
        self.discovery(
            "sensor",
            object_id + "_battery_voltage",
            {
                **common,
                "name": "Battery voltage",
                "device_class": "voltage",
                "state_class": "measurement",
                "unit_of_measurement": "mV",
                "state_topic": f"{root}/battery",
                "value_template": "{{ value_json.millivolts }}",
                "entity_category": "diagnostic",
                "unique_id": object_id + "_battery_voltage",
            },
        )

    def _calibration(
        self, root: str, object_id: str, common: dict[str, Any], state: str
    ) -> None:
        self.publish(f"{root}/calibration", state)
        self.discovery(
            "sensor",
            object_id + "_calibration",
            {
                **common,
                "name": "Calibration",
                "device_class": "enum",
                "options": CALIBRATION_OPTIONS,
                "state_topic": f"{root}/calibration",
                "entity_category": "diagnostic",
                "unique_id": object_id + "_calibration",
            },
        )

    def _temperature(
        self, root: str, object_id: str, common: dict[str, Any], celsius: float
    ) -> None:
        self.publish(f"{root}/temperature", f"{celsius:.1f}")
        self.discovery(
            "sensor",
            object_id + "_temperature",
            {
                **common,
                "name": "Temperature",
                "device_class": "temperature",
                "state_class": "measurement",
                "unit_of_measurement": "°C",
                "state_topic": f"{root}/temperature",
                "unique_id": object_id + "_temperature",
            },
        )

    def _pressure(
        self, root: str, object_id: str, common: dict[str, Any], hpa: float
    ) -> None:
        self.publish(f"{root}/pressure", f"{hpa:.1f}")
        self.discovery(
            "sensor",
            object_id + "_pressure",
            {
                **common,
                "name": "Pressure",
                "device_class": "pressure",
                "state_class": "measurement",
                "unit_of_measurement": "hPa",
                "state_topic": f"{root}/pressure",
                "unique_id": object_id + "_pressure",
            },
        )

    def _siren_patterns(
        self, root: str, object_id: str, common: dict[str, Any], version: str
    ) -> None:
        """Vyber zvukoveho vzoru sireny.

        Uzel prehravany vzor nikam nehlasi, takze stav drzi jen brana - proto se
        publikuje az v okamziku prikazu.
        """
        if version[:8] < SIREN_PATTERN_MIN_VERSION:
            return
        self.discovery(
            "select",
            object_id + "_pattern",
            {
                **common,
                "name": "Siren pattern",
                "icon": "mdi:music-note",
                "command_topic": f"{root}/command",
                "command_template": "pattern_{{ value }}",
                "options": list(SIREN_PATTERNS),
                "state_topic": f"{root}/pattern",
                "unique_id": object_id + "_pattern",
            },
        )

    def _position(
        self, root: str, object_id: str, common: dict[str, Any], type_code: str, payload: str
    ) -> None:
        """Okenni a dvernni senzor hlasi tri polohy: open, close a tilt.

        Do HA se to promita jako kontaktni binary_sensor (sklopene okno je taky
        otevrene), samostatny binary_sensor pro sklopeni a textovy senzor s
        presnou polohou.
        """
        self.publish(f"{root}/position", "closed" if payload == "close" else payload)
        self.publish(f"{root}/contact", "OFF" if payload == "close" else "ON")
        self.publish(f"{root}/tilt", "ON" if payload == "tilt" else "OFF")

        name, device_class = CONTACT_KINDS.get(type_code, ("Opening", "opening"))
        # Uzel, ktery hlasi polohu, je z definice zkalibrovany - pred kalibraci
        # posila jen calreq.
        self._calibration(root, object_id, common, "ok")
        self.discovery(
            "binary_sensor",
            object_id + "_contact",
            {
                **common,
                "name": name,
                "device_class": device_class,
                "state_topic": f"{root}/contact",
                "payload_on": "ON",
                "payload_off": "OFF",
                "unique_id": object_id + "_contact",
            },
        )
        self.discovery(
            "sensor",
            object_id + "_position",
            {
                **common,
                "name": "Position",
                "state_topic": f"{root}/position",
                "unique_id": object_id + "_position",
            },
        )
        # U univerzalniho senzoru se entita sklopeni zaklada az ve chvili, kdy
        # sklopeni opravdu nahlasi - nalepeny na dverich by zustala navzdy
        # prazdna.
        if type_code == "ws02" or payload == "tilt":
            self.discovery(
                "binary_sensor",
                object_id + "_tilt",
                {
                    **common,
                    "name": "Tilted",
                    "state_topic": f"{root}/tilt",
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "unique_id": object_id + "_tilt",
                },
            )

    def _trigger(
        self,
        root: str,
        object_id: str,
        type_code: str,
        device_id: str,
        payload: str,
        base: str,
        timestamp: Any,
    ) -> None:
        """Stisk tlacitka vystavit dvema zpusoby.

        Device trigger je to, co se pouziva v automatizacich, ale v rozhrani HA
        neni nikde videt.  Vedle nej se proto drzi i obycejny senzor s poslednim
        stiskem, aby slo zarizeni zkontrolovat okem.
        """
        subtype = BUTTON_SUBTYPES.get(payload, payload)
        self.discovery(
            "device_automation",
            f"{object_id}_{payload}",
            {
                "automation_type": "trigger",
                "type": "button_short_press",
                "subtype": subtype,
                "topic": f"{root}/button",
                "payload": payload,
                "device": self.device(type_code, device_id, base),
            },
        )
        self.publish(f"{root}/button", payload, retain=False)

        # Entita typu "event" se v HA aktualizuje i pri opakovanem stisku
        # tehoz tlacitka - obycejny senzor by drzel stejnou hodnotu a navenek
        # by to vypadalo, ze se nic nestalo.  Nesmi byt retained.
        self.discovery(
            "event",
            object_id + "_button",
            {
                **self._stateless(self._common(type_code, device_id, base)),
                "name": "Button",
                "device_class": "button",
                "state_topic": f"{root}/event",
                "event_types": sorted(set(BUTTON_SUBTYPES.values())),
                "unique_id": object_id + "_button",
            },
        )
        self.publish(
            f"{root}/event",
            json.dumps(
                {"event_type": subtype, "payload": payload, "timestamp": timestamp},
                separators=(",", ":"),
            ),
            retain=False,
        )

    def remove_device(self, root: str, object_id: str) -> None:
        """Smazat vsechny stopy po odparovanem uzlu.

        Discovery dokumenty i stavy jsou retained, takze by zarizeni v Home
        Assistantu zustalo i po odparovani.  Prazdna retained zprava zaznam na
        brokeru zrusi a HA entitu odstrani.
        """
        if self.client is None:
            return
        topics = {
            f"{self.discovery_prefix}/{component}/{object_id}{suffix}/config"
            for component, suffix in DISCOVERY_SUFFIXES
        }
        topics.update(
            f"{self.discovery_prefix}/device_automation/{object_id}_{payload}/config"
            for payload in BUTTON_SUBTYPES
        )
        # Po restartu brany je mnozina announced prazdna, proto se maze podle
        # pevneho seznamu; announced pokryva pripadne dalsi temata z behu.
        topics.update(
            topic for topic in self.announced if f"/{object_id}" in topic
        )
        for topic in topics:
            self.client.publish(topic, "", retain=True)
            self.announced.discard(topic)
        for name in STATE_TOPICS:
            self.client.publish(f"{root}/{name}", "", retain=True)

    def remove_base(self, base_key: str) -> None:
        """Smazat entity zakladny, ktera se prejmenovala."""
        if self.client is None:
            return
        object_id = f"gigaset_base_{base_key}"
        root = f"{self.base_topic}/base/{base_key}"
        for component, suffix in BASE_DISCOVERY_SUFFIXES:
            topic = f"{self.discovery_prefix}/{component}/{object_id}{suffix}/config"
            self.client.publish(topic, "", retain=True)
            self.announced.discard(topic)
        for name in BASE_STATE_TOPICS:
            self.client.publish(f"{root}/{name}", "", retain=True)
        print(f"MQTT odstraněna základna {base_key}", flush=True)
        print(f"MQTT odstraneno {object_id}", flush=True)

    def publish_event(self, event: dict[str, Any], base: str = "") -> None:
        if self.client is None:
            return
        type_code = event["device_type"]
        device_id = event["device_id"]
        sink = event["sink"]
        payload = str(event.get("payload") or "")
        root = f"{self.base_topic}/{type_code}/{device_id}"
        object_id = f"gigaset_{type_code}_{device_id}"
        common = self._common(type_code, device_id, base)

        self._device_controls(root, object_id, common, type_code)
        self.publish(f"{root}/raw", json.dumps(event, separators=(",", ":")))

        if sink == "ba":
            self._battery(root, object_id, common, payload)
            return

        if sink in {"ver", "hwver"}:
            self.publish(f"{root}/{sink}", payload)
            self.discovery(
                "sensor",
                f"{object_id}_{sink}",
                {
                    **common,
                    "name": "Firmware" if sink == "ver" else "Hardware",
                    "state_topic": f"{root}/{sink}",
                    "entity_category": "diagnostic",
                    "unique_id": f"{object_id}_{sink}",
                },
            )
            if sink == "ver" and type_code == "is01":
                self._siren_patterns(root, object_id, common, payload)
            return

        if not payload:
            return

        # Teplotu hlasi jen nektere uzly (um01) a pod stejnymi sinky, pod
        # jakymi jine posilaji text, takze rozhoduje az tvar hodnoty.
        if sink in TEMPERATURE_SINKS:
            celsius = temperature_from_payload(sink, payload)
            if celsius is not None:
                self._temperature(root, object_id, common, celsius)
                # "tp" nese tlak jako druhou polozku seznamu oddeleneho
                # carkami (napr. "tp=+24.5,990.012") - dosud se zahazovala,
                # takze tlak v HA nikdy neaktualizoval odpoved na periodicky
                # dotaz "temp", jen na samostatny (a nespolehlivy) "press".
                if sink == "tp":
                    parts = payload.split(",")
                    if len(parts) >= 2:
                        hpa = pressure_from_payload(parts[1])
                        if hpa is not None:
                            self._pressure(root, object_id, common, hpa)
                return

        if sink == PRESSURE_SINK:
            hpa = pressure_from_payload(payload)
            if hpa is not None:
                self._pressure(root, object_id, common, hpa)
                return

        if payload == "deleted":
            self.remove_device(root, object_id)
            return

        if type_code in POSITION_TYPES and payload in POSITION_PAYLOADS:
            self._position(root, object_id, common, type_code, payload)
            return

        if payload in CALIBRATION_STATES:
            self._calibration(
                root,
                object_id,
                common,
                CALIBRATION_STATES[payload],
            )
            return

        if type_code == "ps02" and payload == "movement":
            self.discovery(
                "binary_sensor",
                object_id + "_motion",
                {
                    **common,
                    "name": "Motion",
                    "device_class": "motion",
                    "state_topic": f"{root}/motion",
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "off_delay": int(self.config.get("motion_off_delay", 60)),
                    "unique_id": object_id + "_motion",
                },
            )
            self.publish(f"{root}/motion", "ON", retain=False)
            return

        if type_code == "is01" and payload.startswith(("siren", "pattern")):
            # Pripona "_dup" je opakovane potvrzeni tehoz stavu, ne novy stav.
            state = payload[: -len("_dup")] if payload.endswith("_dup") else payload
            self.publish(
                f"{root}/siren",
                "ON" if state in {"sirenon", "patternon"} else "OFF",
            )
            if state == "patternoff":
                # Vzor dohral sam; bez toho by v HA zustal vybrany donekonecna.
                self.publish(f"{root}/pattern", SIREN_PATTERN_OFF)
            self.discovery(
                "siren",
                object_id + "_siren",
                {
                    **common,
                    "name": "Siren",
                    "state_topic": f"{root}/siren",
                    "state_on": "ON",
                    "state_off": "OFF",
                    "command_topic": f"{root}/command",
                    "payload_on": "siren_on",
                    "payload_off": "siren_off",
                    # Bez toho by HA posilal JSON s duration/volume misto holeho
                    # payloadu; uzel nic z toho neumi.
                    "support_duration": False,
                    "support_volume_set": False,
                    "unique_id": object_id + "_siren",
                },
            )
            # Do verze s rucnim ovladanim to byl binary_sensor plus tlacitko na
            # vypnuti; discovery je retained, takze by v HA zustaly viset vedle
            # nove entity.
            self.remove_discovery("binary_sensor", object_id + "_siren")
            self.remove_discovery("button", object_id + "_siren_off")
            return

        if payload in BUTTON_SUBTYPES:
            self._trigger(
                root,
                object_id,
                type_code,
                device_id,
                payload,
                base,
                event.get("timestamp"),
            )
            return

        self.publish(f"{root}/last_event", payload)
        self.discovery(
            "sensor",
            object_id + "_event",
            {
                **common,
                "name": "Last event",
                "state_topic": f"{root}/last_event",
                "entity_category": "diagnostic",
                "unique_id": object_id + "_event",
            },
        )


class Gateway:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.state_path = Path(config.get("state_file", "gigaset_state.json"))
        self.log_path = Path(config.get("event_log", "gigaset_events.jsonl"))
        self.command_state_path = Path(
            config.get("command_state_file", "gigaset_command_state.json")
        )
        command_queue_file = config.get("command_queue_file")
        self.command_queue_path = Path(command_queue_file) if command_queue_file else None
        if self.state_path.exists():
            try:
                self.state = load_config(self.state_path)
            except (OSError, json.JSONDecodeError):
                self.state = {}
        else:
            self.state = {}
        # Odparovane uzly se nesmi pri obnove po startu znovu ohlasit.  Starsi
        # verze brany je ve stavu nechavala, proto se filtruje i pri nacteni.
        self.state = {
            key: device
            for key, device in self.state.items()
            if not is_deleted_device(device)
        }
        self.lock = threading.Lock()
        self.command_lock = threading.Lock()
        self.sent_command_ids: set[str] = set()
        command_state: dict[str, Any] = {}
        if self.command_state_path.exists():
            try:
                command_state = load_config(self.command_state_path)
                self.sent_command_ids.update(command_state.get("sent_command_ids", []))
            except (OSError, json.JSONDecodeError, AttributeError):
                command_state = {}
        self.pending_commands = self._load_event_commands(
            config.get("event_commands", [])
        )
        control = config.get("control", {})
        self.control_enabled = bool(control.get("enabled"))
        self.control_path = (
            Path(control["request_file"]) if control.get("request_file") else None
        )
        self.control_library = str(control.get("library", "gwctl"))
        self.control_dir = (
            Path(control["library_dir"]) if control.get("library_dir") else None
        )
        # Manifest kazde zakladny zvlast (base_key -> cesta), sestaveny v run.sh
        # ze souboru cre_manifest.<base_key>.json - viz _manifest_path_for().
        # Spatny manifest zakladne nenavratne smaze jeji vlastni pravidla, proto
        # tady zadny fallback na "vychozi soubor pro kohokoliv neznameho" neni.
        self.control_manifest_paths: dict[str, Path] = {
            key: Path(value)
            for key, value in config.get("cre_manifest_map", {}).items()
        }
        # Kam ukladat manifest, ktery si zakladna sama nahlasila - jen diagnostika
        # / zaloha pro uzivatele, ne neco, co se zakladne posila zpet, takze tu
        # fallback na spolecny soubor vadit nemuze.
        self.base_manifest_paths: dict[str, Path] = {
            key: Path(value)
            for key, value in config.get("base_manifest_map", {}).items()
        }
        self.control_poll_url = str(control.get("poll_url", ""))
        self.control_poll_port = int(control.get("poll_port", CONTROL_POLL_PORT))
        self.control_poll_interval = int(
            control.get("poll_interval", CONTROL_POLL_INTERVAL)
        )
        # Adresa, na kterou se zakladna dovolala.  Bez vyplneneho poll_url se z ni
        # sklada URL ridiciho kanalu, takze ji nikdo nemusi zadavat rucne.
        self.local_address = ""
        # Fronta cekajicich prikazu pro /gwctl, samostatna pro kazdou zakladnu (IP
        # peeru), aby prikaz urceny pro jednu zakladnu neskoncil u druhe - viz
        # take_control_poll_lines().
        self.control_poll_queues: dict[str, list[dict[str, str]]] = {}
        self.control_poll_lock = threading.Lock()
        self.control_poll_seen = False
        # Both of these must survive a gateway restart: otherwise every request
        # still listed in the request file would be executed again, and the
        # library name could collide with a version the base already cached.
        self.control_serial = max(
            int(control.get("serial", 1)), int(command_state.get("control_serial", 0))
        )
        self.handled_control_ids: set[str] = set(self.sent_command_ids)
        self.handled_control_ids.update(command_state.get("handled_control_ids", []))
        # Mapovani IP -> ID zakladny z podepsaneho certifikatu.  Prezije restart,
        # protoze CSR chodi jen pri obnove certifikatu.
        self.base_ids: dict[str, str] = dict(command_state.get("base_ids", {}))
        self.base_ids.update(config.get("base_ids", {}))
        # Rucne zadane ID zakladny ma prednost pred vsim ostatnim.  Bez nej
        # zacina instance s prazdnym stavem znovu u adresy a v Home Assistantu
        # vznikne druha zakladna vedle te, kterou zalozila instance predchozi.
        self.base_id = re.sub(
            r"[^0-9a-z_-]", "_", str(config.get("base_id", "")).strip().lower()
        )
        # Vsechny klice, pod kterymi uz brana zakladnu v MQTT ohlasila.  Ty,
        # ktere prestaly platit, se pri prvnim kontaktu smazou.
        self.base_keys_seen: dict[str, str] = dict(
            command_state.get("base_keys_seen", {})
        )
        # Posledni potvrzeny rezim alarmu.  Bez toho by se po restartu brany
        # prepsal vychozi hodnotou z konfigurace zakladny a HA by ukazoval
        # rezim, ve kterem zakladna vubec neni.
        self.alarm_modes: dict[str, str] = dict(command_state.get("alarm_modes", {}))
        # Otisk manifestu kazde zakladny (cesta k souboru -> otisk), ktery uz
        # dostala.  Nova knihovna se k ni jinak nedostane - konfiguraci cte jen
        # na vyzvu.  Klicovano cestou, ne peerem/base_key: nekolik peeru muze
        # sdilet stejny soubor, a cesta jednoznacne prezije i pripadnou zmenu
        # base_id.
        self.manifest_digests: dict[str, str] = dict(
            command_state.get("manifest_digests", {})
        )
        # Udrzovaci prikazy (napr. pozadavek na reload konfigurace) cekajici na
        # kazdou zakladnu zvlast - viz control_poll_queues vyse, stejny duvod.
        self.control_commands_by_peer: dict[str, list[dict[str, Any]]] = {}
        self.announced_bases: set[str] = set()
        # Ke kterym peeru (zakladne) naposledy patrilo dane zarizeni - "typ/id" ->
        # IP peeru. Umoznuje smerovat prikaz pro konkretni senzor na tu zakladnu,
        # ktera od nej naposledy videla udalost, misto aby se posilal naslepo.
        self.device_base: dict[str, str] = dict(command_state.get("device_base", {}))
        # Vychozi rezim alarmu z konfigurace, kterou zakladne servirujeme.
        try:
            base_configuration = load_config(Path(config["base_configuration_file"]))
        except (OSError, json.JSONDecodeError, KeyError):
            base_configuration = {}
        self.initial_alarm_mode = str(
            base_configuration.get("last_intrusion_mode", "")
        )
        # Pozadavky z MQTT prichazi z jineho vlakna nez obsluha HTTP.
        self.mqtt_control_lock = threading.Lock()
        self.mqtt_control_requests: list[dict[str, str]] = []
        self.mqtt_control_counter = 0
        self.mqtt = MqttBridge(config.get("mqtt", {}), self.request_control_action)
        self._ensure_control_library()
        self._ensure_manifest_reload()
        self._check_cre_sources()

    def _ensure_control_library(self) -> None:
        """Nahrat aktualni verzi gwctl a zapsat ji do manifestu kazde zname zakladny.

        Knihovna uz neobsahuje jednotlive prikazy, takze se meni jen pri zmene
        sablony nebo konfigurace pollingu, a jeji obsah je pro vsechny zakladny
        stejny (stejna adresa doplnku, stejne URL rizeni) - generuje se tedy
        jen jednou. Zaznam v manifestu se ale musi zapsat do souboru KAZDE
        zname zakladny zvlast, kazda ma svuj vlastni (viz control_manifest_paths).
        Kazda zmena stoji dotcenou zakladnu kompletni reload CRE (16-26 s),
        proto se dela jen kdyz je opravdu potreba.
        """
        if not self.control_enabled or self.control_dir is None:
            return
        url = self.control_poll_url
        if not url:
            if not self.local_address:
                # Adresa se dozvime az z prvniho spojeni od zakladny.
                return
            url = f"http://{self.local_address}:{self.control_poll_port}/gwctl"
        source = control_lua_source(url, self.control_poll_interval)
        current = self.control_dir / f"{self.control_library}-{self.control_serial}.lua"
        if not (current.exists() and current.read_text(encoding="utf-8") == source):
            self.control_serial += 1
            filename = f"{self.control_library}-{self.control_serial}.lua"
            self.control_dir.mkdir(parents=True, exist_ok=True)
            (self.control_dir / filename).write_text(source, encoding="utf-8")
            self._save_command_state()
            print(f"CONTROL nasazuji {filename}", flush=True)

        filename = f"{self.control_library}-{self.control_serial}.lua"
        entry_url = (
            f"/api/v1/bs01/configuration/libs/{self.control_library}/{filename}"
        )
        for path in set(self.control_manifest_paths.values()):
            try:
                manifest = load_config(path)
            except (OSError, json.JSONDecodeError):
                continue
            libraries = manifest.setdefault("endnode_libraries", {})
            if libraries.get(self.control_library) == entry_url:
                continue
            libraries[self.control_library] = entry_url
            atomic_json_write(path, manifest)
            self._request_reload(
                f"control-{self.control_serial}", self._peers_for_manifest_path(path)
            )

    def store_base_inventory(self, document: bytes, peer: str) -> bool:
        """Slozit manifest z vypisu souboru, ktere zakladna ma na disku."""
        manifest = manifest_from_inventory(document.decode("utf-8", "replace"))
        libraries = manifest.get("endnode_libraries", {})
        if len(libraries) < 10:
            print(
                f"CONTROL INVENTÁŘ vypadá neúplně ({len(libraries)} knihoven), "
                "ignoruji",
                flush=True,
            )
            return False
        print(
            "CONTROL INVENTÁŘ "
            + ", ".join(f"{group} {len(manifest[group])}" for group in CRE_GROUPS),
            flush=True,
        )
        # Jen zaloha/diagnostika pro uzivatele - zivy manifest se odsud uz
        # neprebira automaticky. Doplnek dnes nenastartuje bez vlastniho
        # manifestu kazde zakladny (viz run.sh), takze zavadeci rezim, ktery
        # kdysi manifest slozil z tohodle vypisu za behu, uz neni potreba.
        self.store_base_manifest(
            json.dumps(manifest).encode("utf-8"), peer, source="inventáře"
        )
        return True

    def store_base_manifest(
        self, document: bytes, peer: str, source: str = "základny"
    ) -> bool:
        """Ulozit manifest, ktery o sobe poslala zakladna.

        Pouzije se az pri pristim startu, takze bezici konfiguraci nerozhodi.
        Soubor dodany uzivatelem se nikdy neprepisuje.  Ma-li tahle zakladna
        svuj vlastni cilovy soubor (base_manifest_map), pouzije se; jinak
        spolecny vychozi - jde jen o diagnostiku/zalohu, ne o neco, co se
        zakladne posila zpet, takze spolecny fallback tu nevadi.
        """
        target = self.base_manifest_paths.get(base_key(peer)) or self.config.get(
            "base_manifest_file"
        )
        if not target:
            return False
        try:
            manifest = json.loads(document.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            print("CONTROL MANIFEST není platný JSON", flush=True)
            return False
        if not isinstance(manifest, dict) or "endnode_libraries" not in manifest:
            print("CONTROL MANIFEST nemá očekávaný tvar", flush=True)
            return False
        path = Path(target)
        summary = (
            f"{len(manifest.get('endnode_libraries', {}))} knihoven, "
            f"{len(manifest.get('rules', {}))} pravidel, "
            f"{len(manifest.get('internal_rules', {}))} interních"
        )
        if path.exists():
            print(f"CONTROL MANIFEST z {source}: {summary} (uložený ponechán)", flush=True)
            return True
        atomic_json_write(path, manifest)
        print(f"CONTROL MANIFEST z {source} uložen do {path}: {summary}", flush=True)
        return True

    def _check_cre_sources(self) -> None:
        """Zapsat do logu, ktere polozky manifestu kazde zname zakladny brana sama neposkytuje.

        Zakladna si stahuje jen ty polozky manifestu, kterym se zmenil nazev
        souboru - puvodni knihovny uz ma z doby, kdy ji provisionoval
        originalni cloud, a tenhle seznam u nich nic nemeni. Manifest je vzdy
        zakladny/uzivatele vlastni (viz cre_manifest_map - brana uz zadny
        vestaveny nahradni manifest nema), takze chybejici stare nazvy jsou
        ocekavane a neskodne - budou potreba az po tovarnim resetu (viz
        firmware_cre_dir v DOCS.md). Zprava je proto jen informativni, ne
        varovani.
        """
        for path in set(self.control_manifest_paths.values()):
            try:
                manifest = load_config(path)
            except (OSError, json.JSONDecodeError):
                continue
            missing = sorted(
                {
                    url.rsplit("/", 1)[-1]
                    for name, group in manifest.items()
                    if isinstance(group, dict)
                    for key, url in group.items()
                    # gwctl si brana generuje sama, az kdyz zna svoji adresu.
                    if not (name == "endnode_libraries" and key == self.control_library)
                    and cre_source(self.config, url.rsplit("/", 1)[-1]) is None
                }
            )
            if not missing:
                continue
            print(
                f"Z firmwaru ({path}) chybí {len(missing)} souborů. Základna je "
                "má na sobě, potřeba budou až po továrním resetu.",
                flush=True,
            )

    def _request_reload(self, reason: str, peers: list[str] | None = None) -> None:
        """Vyzvat zakladnu (nebo zakladny), aby si znovu nactly konfiguraci.

        `peers=None` znamena "vsechny zatim zname zakladny" - vhodne, kdyz
        volajici jeste neresi konkretni manifest (napr. rucne zadany
        pozadavek bez "base"); s jednou zakladnou se mnozina zredukuje na tu
        jednu, takze se chova stejne jako drive.
        """
        command = {
            "id": f"reload-{reason}",
            "type": "raw",
            "message": {"type": "configuration-changed"},
        }
        targets = peers if peers is not None else list(self.base_keys_seen)
        for target in targets:
            self.control_commands_by_peer.setdefault(target, []).append(dict(command))

    def _ensure_manifest_reload(self) -> None:
        """Vyzvat kazdou znamou zakladnu k nacteni konfigurace, zmenil-li se jeji CRE manifest.

        Zakladna si konfiguraci sama nevyzvedava, takze bez teto zpravy by na ni
        nova knihovna z aktualizace doplnku nikdy nedorazila. Kazdy soubor
        manifestu se sleduje zvlast (viz manifest_digests) a reload se zada
        jen u zakladen, ktere ho skutecne pouzivaji.
        """
        for path in set(self.control_manifest_paths.values()):
            try:
                manifest = load_config(path)
            except (OSError, json.JSONDecodeError):
                continue
            digest = hashlib.sha256(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:16]
            digest_key = str(path)
            if digest == self.manifest_digests.get(digest_key):
                continue
            self.manifest_digests[digest_key] = digest
            self._save_command_state()
            peers = self._peers_for_manifest_path(path)
            already_queued = any(
                item.get("message", {}).get("type") == "configuration-changed"
                for peer in peers
                for item in self.control_commands_by_peer.get(peer, [])
            )
            if already_queued:
                continue
            print(f"CONTROL manifest CRE ({path}) se změnil, žádám o reload", flush=True)
            self._request_reload(f"manifest-{digest}", peers)

    def take_control_poll_lines(self, peer: str) -> str:
        """Vybrat cekajici prikazy pro gwctl teto konkretni zakladny. Ctou se jen jednou."""
        # Dotaz od zakladny je posledni okamzik, kdy jeste jde zachytit
        # pozadavek z MQTT nebo ze souboru - jinak by cekal na dalsi kolo.
        with self.command_lock:
            try:
                self._refresh_control_requests()
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                print(f"Nelze zpracovat control požadavky: {exc}", flush=True)
        with self.control_poll_lock:
            queued = self.control_poll_queues.pop(peer, [])
        if not queued:
            return ""
        for request in queued:
            print(
                f"CONTROL POLL {request['id']}: {request['action']} "
                f"{request['device_type']} {request['device_id']}".rstrip(),
                flush=True,
            )
        return "".join(control_poll_line(request) + "\n" for request in queued)

    def _resolve_target_peers(self, request: dict[str, str]) -> list[str]:
        """Na ktere zakladny (IP peeru) se ma pozadavek doruvcit.

        Pozadavky vazane na konkretni zarizeni jdou tam, kde to zarizeni bylo
        naposledy videno; kdyz to nevime (nove zarizeni), rozeslou se na vsechny
        zname zakladny - pro zakladnu, ktera to zarizeni nema sparovane, je to
        neskodne, protoze nema komu ULE prikaz predat. Pozadavky vazane na
        zakladnu (parovani, sirena zakladny, rezim alarmu...) potrebuji
        explicitni cil, jakmile je znama vic nez jedna zakladna.
        """
        action = request.get("action", "")
        device_id = request.get("device_id", "")
        device_type = request.get("device_type", "")
        if action in CONTROL_ACTIONS_NEEDING_DEVICE and device_id:
            peer = self.device_base.get(f"{device_type}/{device_id}")
            if peer:
                return [peer]
            return list(self.base_keys_seen)
        base_hint = str(request.get("base", ""))
        if base_hint:
            peer = self._peer_for_base_key(base_hint)
            return [peer] if peer else []
        if len(self.base_keys_seen) <= 1:
            return list(self.base_keys_seen)
        print(
            f"CONTROL {request.get('id', '')}: akce {action} nemá určenou "
            'základnu a je jich známo víc - zahazuji, přidejte pole "base"',
            flush=True,
        )
        return []

    def _forget_device(self, device_type: str, device_id: str) -> None:
        """Smazat mistni evidenci zarizeni, aniz by se cekalo na potvrzeni od zakladny.

        "unpair" posila zakladne prikaz "delete" a mistni zaznam smaze az
        potvrzujici udalost "deleted" (viz publish_event) - takze pro
        zarizeni sparovane na zakladne, ktera uz k teto instanci brany
        nemluvi (vymenena, offline, jina session), by cekalo navzdy. Tohle
        smaze stav i retained discovery rovnou, bez zadneho spojeni se
        zakladnou.
        """
        if ENDNODE_TYPE_RE.fullmatch(device_type) is None or ENDNODE_ID_RE.fullmatch(device_id) is None:
            print(f"CONTROL FORGET odmítnuto: neplatný typ/id {device_type}/{device_id}", flush=True)
            return
        key = f"{device_type}/{device_id}"
        with self.lock:
            existed = self.state.pop(key, None) is not None
            device_base_changed = self.device_base.pop(key, None) is not None
            atomic_json_write(self.state_path, self.state)
        if device_base_changed:
            self._save_command_state()
        self.mqtt.remove_device(
            f"{self.mqtt.base_topic}/{device_type}/{device_id}",
            f"gigaset_{device_type}_{device_id}",
        )
        note = "" if existed else " (v evidenci nebylo)"
        print(f"CONTROL FORGET {key} smazáno lokálně{note}", flush=True)

    def request_control_action(
        self, action: str, device_type: str, device_id: str, base_key: str = ""
    ) -> None:
        """Zaradit pozadavek z MQTT do stejne fronty jako soubor s pozadavky."""
        if action == "forget":
            # Zadny prikaz zadne zakladne - viz _forget_device.
            self._forget_device(device_type, device_id)
            return
        with self.mqtt_control_lock:
            self.mqtt_control_counter += 1
            item = {
                "id": f"mqtt-{int(time.time())}-{self.mqtt_control_counter}",
                "action": action,
                "device_type": device_type,
                "device_id": device_id,
            }
            try:
                normalized = normalize_control_request(item)
            except ValueError as error:
                print(f"CONTROL MQTT odmítnuto: {error}", flush=True)
                return
            normalized["base"] = base_key
            self.mqtt_control_requests.append(normalized)
        print(
            f"CONTROL MQTT {normalized['id']}: {action} "
            f"{device_type} {device_id}".rstrip(),
            flush=True,
        )
        if not self.control_poll_seen:
            print(
                "CONTROL POZOR: základna si zatím ani jednou nepřišla pro příkaz, "
                f"zkontrolujte dostupnost http://{self.local_address or '?'}:"
                f"{self.control_poll_port}/gwctl z její sítě",
                flush=True,
            )

    def note_control_poll(self, peer: str) -> None:
        """Ohlasit prvni uspesny dotaz gwctl.

        Prazdna fronta se odbavuje potichu, takze nefunkcni ridici kanal by se
        jinak poznal jen podle toho, ze se nic nedeje.
        """
        if self.control_poll_seen:
            return
        self.control_poll_seen = True
        print(f"CONTROL POLL kanál navázán z {peer}", flush=True)

    def _save_command_state(self) -> None:
        atomic_json_write(
            self.command_state_path,
            {
                "sent_command_ids": sorted(self.sent_command_ids),
                "handled_control_ids": sorted(self.handled_control_ids),
                "control_serial": self.control_serial,
                "base_ids": self.base_ids,
                "base_keys_seen": self.base_keys_seen,
                "alarm_modes": self.alarm_modes,
                "manifest_digests": self.manifest_digests,
                "device_base": self.device_base,
            },
        )

    def note_local_address(self, address: str) -> None:
        """Zapamatovat adresu, na kterou se zakladna dovolala.

        Je to presne ta adresa, kterou ma pouzit i pro ridici kanal - vcetne
        pripadu, kdy provoz prochazi pres DNAT nebo ma brana vic rozhrani.
        """
        if not address or address == self.local_address:
            return
        self.local_address = address
        print(f"ADRESA BRÁNY {address} (podle spojení od základny)", flush=True)
        if not self.control_poll_url:
            self._ensure_control_library()
            self._ensure_manifest_reload()

    def base_name(self, peer: str) -> str:
        """Stabilni klic zakladny.

        Adresa je jedina hodnota, kterou zna kazda instance brany od prvniho
        pozadavku, takze vsechny skonci u stejneho zarizeni v Home Assistantu.
        Rucne zadane ID ma prednost, kdyby zakladna adresu presto zmenila -
        ale jen dokud jde o jedinou znamou zakladnu.  Se dvema fyzickymi
        zakladnami by "base_id" obe tise slilo do jedne entity v HA, proto se
        pro kazdeho dalsiho peera pouzije zase adresa (viz record()).
        """
        if self.base_id and (not self.base_keys_seen or set(self.base_keys_seen) <= {peer}):
            return self.base_id
        return base_key(peer)

    def remember_base_identity(self, peer: str, identity: str) -> None:
        """Zapamatovat ID zakladny z certifikatu nebo z hlaseni o pravidle.

        Klicem uz neni - slouzi jen jako entita "Base identifier" a jako
        kandidat na uklid entit, ktere pod nim zalozily starsi verze brany.
        """
        if not identity or self.base_ids.get(peer) == identity:
            return
        self.base_ids[peer] = identity
        self._save_command_state()
        print(f"BASE ID {peer} -> {identity}", flush=True)
        if identity != self.base_name(peer) and identity not in self.base_keys_seen.values():
            # Starsi verze brany zakladnu klicovaly podle ID, takze pod nim v
            # Home Assistantu zustalo druhe, uz neaktualizovane zarizeni.
            self._retire_base_key(identity)

    def _retire_stale_base_keys(self, base: str, peer: str) -> None:
        """Smazat entity zakladny, ktera se drive jmenovala jinak.

        Starsi verze brany klicovaly zakladnu podle ID z certifikatu, takze po
        prechodu na adresu by v Home Assistantu zustala druha, mrtva zakladna a
        uzly by se mezi ne rozdelily podle toho, ktera brana je zrovna ohlasila.
        """
        if self.base_keys_seen.get(peer) == base:
            return
        # Klice ostatnich zakladen jsou tabu - na jednom brokeru jich muze byt vic.
        in_use = {
            key for other, key in self.base_keys_seen.items() if other != peer
        }
        stale = {
            self.base_keys_seen.get(peer, ""),
            self.base_ids.get(peer, ""),
        } - {"", base} - in_use
        self.base_keys_seen[peer] = base
        self._save_command_state()
        for key in sorted(stale):
            self._retire_base_key(key)

    def _retire_base_key(self, key: str) -> None:
        self.mqtt.remove_base(key)
        # Uzly nesou via_device se starym klicem, takze se musi ohlasit znovu.
        self.mqtt.announced.clear()

    def _refresh_control_requests(self) -> None:
        """Zaradit nove pozadavky do fronty, kterou si gwctl sam vyzvedne."""
        if not self.control_enabled or self.control_path is None:
            return
        if self.control_dir is None or not self.control_path.exists():
            return
        try:
            document = load_config(self.control_path)
        except (OSError, json.JSONDecodeError):
            return

        fresh: list[dict[str, str]] = []
        for item in document.get("requests", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("id", "")) in self.handled_control_ids:
                continue
            if not str(item.get("id", "")):
                continue
            normalized = normalize_control_request(item)
            # Volitelne pole "base" (base_key) rika, ktere zakladne pozadavek
            # patri - potreba jen u akci vazanych na zakladnu, jakmile je jich
            # znamo vic nez jedna. normalize_control_request ho nezna, tak se
            # doplnuje az sem.
            normalized["base"] = str(item.get("base", ""))
            fresh.append(normalized)

        with self.mqtt_control_lock:
            queued = self.mqtt_control_requests
            self.mqtt_control_requests = []
        fresh.extend(
            item for item in queued if item["id"] not in self.handled_control_ids
        )

        if not fresh:
            return

        with self.control_poll_lock:
            for request in fresh:
                for target_peer in self._resolve_target_peers(request):
                    self.control_poll_queues.setdefault(target_peer, []).append(request)
        for request in fresh:
            self.handled_control_ids.add(request["id"])
        self._save_command_state()

    def _refresh_event_commands(self) -> None:
        if self.command_queue_path is None or not self.command_queue_path.exists():
            return
        queue_config = load_config(self.command_queue_path)
        commands = (
            queue_config.get("event_commands", [])
            if isinstance(queue_config, dict)
            else queue_config
        )
        self.pending_commands = self._load_event_commands(commands)

    def _load_event_commands(self, commands: Any) -> list[dict[str, Any]]:
        if not isinstance(commands, list):
            raise ValueError("event_commands must be a list")
        pending: list[dict[str, Any]] = []
        known_ids = set(self.sent_command_ids)
        for item in commands:
            if not isinstance(item, dict):
                raise ValueError("Each event command must be a JSON object")
            command_id = str(item.get("id", ""))
            event_type = str(item.get("type", ""))
            if not command_id or command_id in known_ids:
                continue
            if event_type == "raw":
                message = item.get("message")
                if not isinstance(message, dict):
                    raise ValueError(f"Raw command {command_id} needs a message object")
                if message.get("type") not in CLOUD_MESSAGE_TYPES:
                    raise ValueError(
                        f"Raw command {command_id} uses unknown cloud message "
                        f"type {message.get('type')!r}"
                    )
                pending.append({"id": command_id, "type": "raw", "message": message})
                known_ids.add(command_id)
                continue
            if event_type != "endnode":
                raise ValueError(f"Unsupported event command type: {event_type}")
            device_id = str(item.get("device_id", ""))
            device_type = str(item.get("device_type", "ws02"))
            command = str(item.get("command", ""))
            if ENDNODE_ID_RE.fullmatch(device_id) is None:
                raise ValueError(f"Invalid endnode device_id: {device_id}")
            if ENDNODE_TYPE_RE.fullmatch(device_type) is None:
                raise ValueError(f"Invalid endnode device_type: {device_type}")
            if command not in SAFE_ENDNODE_COMMANDS:
                raise ValueError(f"Unsafe endnode command: {command}")
            pending.append(
                {
                    "id": command_id,
                    "type": event_type,
                    "device_type": device_type,
                    "device_id": device_id.lower(),
                    "command": command,
                }
            )
            known_ids.add(command_id)
        return pending

    def _matches_peer(self, item: dict[str, Any], peer: str) -> bool:
        """Patri tenhle pozadavek zakladne s danou IP?

        Konkretni zarizeni se resi podle toho, ktera zakladna od nej naposledy
        videla udalost; udrzovaci akce podle volitelneho pole "base" (base_key).
        Kdyz neni k dispozici ani jedno a je znama nejvyse jedna zakladna, chova
        se to jako dnes - proslo by vsechno.  Jakmile je znama vic nez jedna
        zakladna a cil se neda urcit, radeji pozadavek jeste chvili pockat, nez
        ho poslat nekomu nespravnemu.
        """
        device_id = item.get("device_id")
        if device_id:
            mapped = self.device_base.get(f"{item.get('device_type', '')}/{device_id}")
            if mapped:
                return mapped == peer
        base_hint = item.get("base", "")
        if base_hint:
            return self._peer_for_base_key(base_hint) == peer
        return len(self.base_keys_seen) <= 1

    def _peer_for_base_key(self, key: str) -> str | None:
        for candidate_peer, seen_key in self.base_keys_seen.items():
            if seen_key == key:
                return candidate_peer
        return None

    def _manifest_path_for(self, peer: str) -> Path | None:
        """Ktery soubor CRE manifestu poslat teto konkretni zakladne.

        Zamerne zadny fallback na "spolecny vychozi soubor" - kdyz pro peera
        neni v cre_manifest_map zaznam, vraci se None a volajici nesmi poslat
        nic (viz DULEZITE u _request_reload): spatny manifest zakladne
        nenavratne smaze jeji vlastni pravidla. run.sh sestavuje mapu pro
        VSECHNY zname zakladny vcetne prvni, takze tohle neni jen fallback
        pro druhou a dalsi - je to jedina cesta.
        """
        return self.control_manifest_paths.get(base_key(peer))

    def _peers_for_manifest_path(self, path: Path) -> list[str]:
        """Kteri znami peeri (zakladny) pouzivaji tenhle konkretni soubor manifestu.

        Porovnava se podle syroveho base_key(peer), stejne jako
        _manifest_path_for - narozdil od _peer_for_base_key to zamerne NENI
        zobrazovane jmeno zakladny (to muze byt prepsane volbou base_id),
        protoze soubory manifestu se jmenuji podle skutecne adresy.
        """
        matching_keys = {
            key for key, value in self.control_manifest_paths.items() if value == path
        }
        return [peer for peer in self.base_keys_seen if base_key(peer) in matching_keys]

    def next_event_command(self, peer: str) -> dict[str, Any] | None:
        with self.command_lock:
            try:
                self._refresh_event_commands()
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                print(f"Nelze načíst frontu příkazů: {exc}", flush=True)
            try:
                self._refresh_control_requests()
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                print(f"Nelze zpracovat control požadavky: {exc}", flush=True)
            # Maintenance requests go first: they are user initiated and the
            # base station only applies one configuration change at a time.
            queue = self.control_commands_by_peer.get(peer, [])
            if queue:
                return dict(queue[0])
            for item in self.pending_commands:
                if self._matches_peer(item, peer):
                    return dict(item)
            return None

    def mark_event_command_sent(self, command_id: str, peer: str) -> None:
        with self.command_lock:
            self.pending_commands = [
                item for item in self.pending_commands if item["id"] != command_id
            ]
            queue = self.control_commands_by_peer.get(peer)
            if queue:
                self.control_commands_by_peer[peer] = [
                    item for item in queue if item["id"] != command_id
                ]
            self.sent_command_ids.add(command_id)
            self._save_command_state()

    def _store_diagnostic(self, body: bytes, peer: str) -> Path:
        target_dir = Path(self.config.get("diagnostic_dir", "gigaset_diagnostic"))
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"diag_{int(time.time())}.bin"
        target.write_bytes(body)
        print(f"DIAG {peer} uloženo {len(body)} B -> {target}", flush=True)
        return target

    @staticmethod
    def _print_cre_log(path: str, text: str) -> None:
        """Vypsat hlaseni z Lua knihoven bezicich na zakladne."""
        match = CRE_DIAG_RE.match(path)
        if match is None:
            return
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {}
        location = str(payload.get("location", "")).rsplit("/", 1)[-1]
        message = payload.get("message", text)
        print(f"CRE {match.group('level').upper()} {location} {message}", flush=True)

    def record(self, method: str, path: str, body: bytes, peer: str) -> None:
        if method == "POST" and path.startswith("/api/v1/bs/sink/diagnostic"):
            # Syslog upload (/mnt/data/log_messages) - can be hundreds of kB and
            # is not UTF-8 clean, so keep it out of the JSONL event log.
            target = self._store_diagnostic(body, peer)
            text = f"<diagnostic upload {len(body)} B -> {target}>"
        else:
            text = body.decode("utf-8", errors="replace")
        record = {
            "received_at": int(time.time()),
            "peer": peer,
            "method": method,
            "path": path,
            "body": text,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

        self._print_cre_log(path, text)
        if path.startswith("/api/v1/bs/sink/unknown"):
            # Sem zakladna hlasi prikaz, kteremu uzel nerozumel (ule/error.c).
            print(f"UZEL ODMÍTL {peer} {text.strip()}", flush=True)

        self.remember_base_identity(peer, base_identity_from_action(path, text))

        if self.base_id and peer not in self.base_keys_seen and self.base_keys_seen:
            print(
                f"POZOR: base_id je nastaveno, ale hlásí se víc než jedna základna "
                f"- pro {peer} se použije adresa místo base_id, aby se dvě fyzické "
                "základny neslily do jedné entity v Home Assistantu",
                flush=True,
            )

        # Tlacitka zakladny se vystavi az ve chvili, kdy se nejaka opravdu ozve.
        base = self.base_name(peer)
        self._retire_stale_base_keys(base, peer)
        self.mqtt.announce_base(
            base,
            self.alarm_modes.get(base) or self.initial_alarm_mode,
            self.base_ids.get(peer, ""),
        )
        confirmed = self.mqtt.publish_base_telemetry(base, peer, path, text)
        if confirmed and self.alarm_modes.get(base) != confirmed:
            self.alarm_modes[base] = confirmed
            self._save_command_state()
            print(f"REZIM {base} -> {confirmed}", flush=True)
        if base not in self.announced_bases:
            self.announced_bases.add(base)
            with self.lock:
                # Jen zarizeni, ktera prokazatelne patri prave teto zakladne -
                # jinak by prvni restart po pridani dalsi zakladny znovu
                # ohlasil UPLNE VSECHNA zarizeni ze sdileneho self.state (i ta
                # jiných základen) pod tou zakladnou, ktera se ozvala jako
                # prvni, a discovery() je kvuli deduplikaci podle tematu uz
                # nikdy neprepublikuje pod tou spravnou.
                known = [
                    item
                    for key, device in self.state.items()
                    for item in device.values()
                    if isinstance(item, dict)
                    and "sink" in item
                    and self.device_base.get(key) == peer
                ]
            self.mqtt.announce_known(known, base)

        match = ENDNODE_RE.match(path)
        if method != "POST" or match is None:
            return
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {"payload": text}
        event = {
            "device_type": match.group("type"),
            "device_id": match.group("id"),
            "sink": match.group("sink"),
            "payload": parsed.get("payload"),
            "timestamp": parsed.get("timestamp"),
            "seq_number": parsed.get("seq_number"),
            "received_at": record["received_at"],
        }
        key = f"{event['device_type']}/{event['device_id']}"
        device_base_changed = False
        with self.lock:
            if event["payload"] == "deleted":
                # Odparovany uzel se nesmi po restartu znovu ohlasit, a prikazy pro
                # nej uz nemaji kam smerovat.
                self.state.pop(key, None)
                device_base_changed = self.device_base.pop(key, None) is not None
            else:
                device = self.state.setdefault(
                    key,
                    {
                        "device_type": event["device_type"],
                        "device_id": event["device_id"],
                    },
                )
                device[event["sink"]] = event
                # Zapamatovat, ktera zakladna od tohohle zarizeni naposledy videla
                # udalost - pouzije se pri smerovani prikazu (viz _resolve_target_peers).
                if self.device_base.get(key) != peer:
                    self.device_base[key] = peer
                    device_base_changed = True
            atomic_json_write(self.state_path, self.state)
        if device_base_changed:
            self._save_command_state()
        self.mqtt.publish_event(event, base)
        # Ohlaseni uzlu (sink/dev) zadny "payload" nema, a prave v nem uzel
        # popisuje sam sebe - bez cele zpravy by to zapadlo.
        detail = "" if event["payload"] is not None else f" raw={text.strip()}"
        print(
            f"EVENT {key} {event['sink']}={event['payload']} "
            f"seq={event['seq_number']} ts={event['timestamp']}{detail}",
            flush=True,
        )

    def snapshot(self) -> bytes:
        with self.lock:
            return json.dumps(self.state, ensure_ascii=False).encode("utf-8")


def certificate_request_name(csr_pem: bytes) -> str:
    """Vytahnout z CSR spolecne jmeno, coz je unikatni ID zakladny.

    Zakladna zadny svuj identifikator v pozadavcich neposila; jedine misto, kde
    o sobe rekne, kdo je, je zadost o podpis certifikatu.
    """
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    try:
        csr = x509.load_pem_x509_csr(csr_pem)
    except ValueError:
        return ""
    names = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not names:
        return ""
    return str(names[0].value).strip().lower()


def sign_client_certificate(csr_pem: bytes, config: dict[str, Any]) -> bytes:
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import ExtendedKeyUsageOID

    csr = x509.load_pem_x509_csr(csr_pem)
    # The legacy base signs its CSR with SHA-1, rejected by current OpenSSL.
    # The returned certificate still binds exactly to the CSR public key.

    signer_cert = x509.load_pem_x509_certificate(
        Path(config["certificate"]).read_bytes()
    )
    signer_key = serialization.load_pem_private_key(
        Path(config["private_key"]).read_bytes(), password=None
    )
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(signer_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .sign(signer_key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


def receive_request(connection: ssl.SSLSocket) -> tuple[str, str, bytes, str]:
    data = bytearray()
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = connection.recv(8192)
        if not chunk:
            break
        data.extend(chunk)
    if b"\r\n\r\n" not in data:
        return "", "", b"", ""
    header, body = bytes(data).split(b"\r\n\r\n", 1)
    first = header.split(b"\r\n", 1)[0].decode("ascii", errors="replace").split()
    method = first[0] if first else ""
    path = first[1] if len(first) > 1 else ""
    content_length = 0
    for line in header.split(b"\r\n")[1:]:
        if line.lower().startswith(b"content-length:"):
            content_length = int(line.split(b":", 1)[1].strip())
            break
    while len(body) < content_length:
        chunk = connection.recv(8192)
        if not chunk:
            break
        body += chunk
    headers = header.decode("utf-8", errors="replace")
    return method, path, body[:content_length], headers


def handle_connection(
    raw: socket.socket,
    peer: tuple[str, int],
    context: ssl.SSLContext,
    gateway: Gateway,
) -> None:
    try:
        raw.settimeout(int(gateway.config.get("socket_timeout_seconds", 120)))
        gateway.note_local_address(raw.getsockname()[0])
        with context.wrap_socket(raw, server_side=True) as tls:
          while True:
            method, path, body, headers = receive_request(tls)
            if gateway.config.get("log_headers") and method:
                print(f"HLAVICKY {peer[0]} {method} {path}\n{headers}", flush=True)
            if not method:
                return
            gateway.record(method, path, body, peer[0])
            if method == "GET" and path == "/api/v1/bs/events":
                long_poll = int(gateway.config.get("event_keepalive_seconds", 20))
                heartbeat_tick = int(gateway.config.get("cloud_heartbeat_tick", 60))
                deadline = time.monotonic() + long_poll
                response_event: dict[str, Any] | None = None
                sent_command: dict[str, Any] | None = None
                while time.monotonic() < deadline:
                    command = gateway.next_event_command(peer[0])
                    if command is not None:
                        response_event = cloud_event_for_command(command)
                        sent_command = command
                        break
                    time.sleep(min(0.5, max(0, deadline - time.monotonic())))
                if response_event is None:
                    response_event = {"type": "heartbeat", "tick": heartbeat_tick}
                # The base station's cloud RX callback reads the events stream
                # line by line, so the JSON message must be newline terminated
                # or it is buffered forever and never dispatched.
                response_body = (
                    json.dumps(response_event, separators=(",", ":")) + "\n"
                ).encode("utf-8")
                response = (
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(response_body)}\r\n".encode("ascii")
                    + b"Connection: keep-alive\r\n\r\n"
                    + response_body
                )
                tls.sendall(response)
                if sent_command is not None:
                    gateway.mark_event_command_sent(sent_command["id"], peer[0])
                    print(
                        "EVENT COMMAND "
                        + str(sent_command["id"])
                        + " -> "
                        + json.dumps(response_event, separators=(",", ":")),
                        flush=True,
                    )
                elif gateway.config.get("log_heartbeats", True):
                    print(
                        f"EVENT HEARTBEAT {peer[0]} tick={heartbeat_tick}",
                        flush=True,
                    )
                continue
            if method == "GET" and path == "/status":
                response_body = gateway.snapshot()
                content_type = "application/json"
            elif method == "GET" and path == "/probe_status":
                response_body = b'{"status":"ok"}'
                content_type = "application/json"
            elif method == "GET" and path == "/api/v1/bs01/configuration":
                manifest_path = gateway._manifest_path_for(peer[0])
                if manifest_path is None:
                    print(
                        f"CONFIG {peer[0]} nemá přiřazený CRE manifest - "
                        f"chybí cre_manifest.{base_key(peer[0])}.json, jinak "
                        "se bude ptát pořád dokola",
                        flush=True,
                    )
                    response = (
                        b"HTTP/1.1 404 Not Found\r\n"
                        b"Content-Length: 0\r\n"
                        b"Connection: close\r\n\r\n"
                    )
                    tls.sendall(response)
                    return
                configuration_payload, cre_uid = local_configuration(
                    gateway.config, manifest_path
                )
                cre_manifest = configuration_payload["cre"]
                response_body = json.dumps(
                    configuration_payload,
                    separators=(",", ":"),
                ).encode("utf-8")
                content_type = "application/json"
                print(
                    f"CONFIG {peer[0]} lokální CRE manifest "
                    f"uid={cre_uid} "
                    f"rules={len(cre_manifest.get('rules', {}))} "
                    f"internal={len(cre_manifest.get('internal_rules', {}))} "
                    f"libs={len(cre_manifest.get('endnode_libraries', {}))}",
                    flush=True,
                )
            elif method == "GET" and CRE_SOURCE_RE.match(path):
                filename = CRE_SOURCE_RE.match(path).group("file")
                source = cre_source(gateway.config, filename)
                if source is None:
                    print(f"CRE SOURCE {peer[0]} {filename} CHYBÍ", flush=True)
                    response = (
                        b"HTTP/1.1 404 Not Found\r\n"
                        b"Content-Length: 0\r\n"
                        b"Connection: close\r\n\r\n"
                    )
                    tls.sendall(response)
                    return
                print(f"CRE SOURCE {peer[0]} {filename} {len(source)} B", flush=True)
                response_body = source
                content_type = "text/plain"
            elif method == "POST" and path == "/api/v1/bs01/configuration":
                try:
                    confirmation = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    confirmation = {}
                manifest_path = gateway._manifest_path_for(peer[0])
                expected_uid = (
                    local_configuration(gateway.config, manifest_path)[1]
                    if manifest_path is not None
                    else None
                )
                feature = confirmation.get("feature")
                status = confirmation.get("status")
                confirmed_uid = confirmation.get("cre_uid")
                valid = (
                    feature in (None, "configuration-confirm")
                    and status == "success"
                    and confirmed_uid == expected_uid
                )
                print(
                    f"CONFIG CONFIRM {peer[0]} "
                    f"feature={feature!r} status={status!r} "
                    f"uid={confirmed_uid!r} valid={valid}",
                    flush=True,
                )
                response_body = b"{}"
                content_type = "application/json"
            elif method == "POST" and path == "/api/v1/bs01/sign":
                response_body = sign_client_certificate(body, gateway.config)
                content_type = "application/x-pem-file"
                identity = certificate_request_name(body)
                if identity:
                    gateway.remember_base_identity(peer[0], identity)
                print(
                    f"CERT SIGN {peer[0]} cn={identity or '?'} platnost=3650 dní",
                    flush=True,
                )
            elif method == "GET":
                response_body = b"[]"
                content_type = "application/json"
            else:
                response_body = b"{}"
                content_type = "application/json"
            response = (
                b"HTTP/1.1 200 OK\r\n"
                + f"Content-Type: {content_type}\r\n".encode("ascii")
                + f"Content-Length: {len(response_body)}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
                + response_body
            )
            tls.sendall(response)
            # The base station expects one request per TLS session on every
            # endpoint except the /api/v1/bs/events long poll.  Keeping the
            # connection open here breaks the configuration handshake (sync LED
            # keeps blinking, cloud LED never lights up).
            return
    except Exception as exc:
        print(f"TLS {peer[0]} failed: {type(exc).__name__}: {exc}", flush=True)
        try:
            raw.close()
        except OSError:
            pass


def raw_dns_loop(interface: str, base_mac: str, target_ip: str) -> None:
    """Odpovi na kazdy A-dotaz teto zakladny nasi adresou.

    Na izolovanem laboratornim segmentu (bez pripojeni k realnemu internetu)
    si zakladna nejdriv potrebuje prelozit jmena NTP serveru
    (europe.pool.ntp.org, ntp.gigaset-elements.com, ...), ne jen cloudovy
    hostname - bez odpovedi na tyhle dotazy se nikdy nedostane ani k pokusu
    o NTP, natoz o cloud (overeno 2026-08-21 na druhe zakladne: desitky
    nezodpovezenych dotazu na NTP jmena, zadny na cloud). Produkcni nasazeni
    tohle nepotrebuje - tam NTP resi presmerovani portu na routeru, ne DNS.
    """
    try:
        from scapy.all import DNS, DNSRR, Ether, IP, UDP, conf, sendp, sniff
    except ImportError as exc:
        raise RuntimeError("Raw DNS vyžaduje balíček scapy a Npcap.") from exc
    import zlib

    iface = conf.ifaces.dev_from_name(interface)
    prefix = target_ip.rsplit(".", 1)[0]
    resolved: dict[str, str] = {}

    def resolve(name: str) -> str:
        # Cloudovy hostname MUSI mirit presne na target_ip - tam posloucha
        # nas HTTPS server. Vsechno ostatni (hlavne jmena NTP serveru) dostane
        # kazde jinou IP v ramci stejne site: realny pool.ntp.org rotuje mezi
        # ruznymi servery, a kdyz vsechna jmena vraceji stejnou adresu, uzel
        # to muze brat jako podezrele a NTP dotaz uz neposlat (overeno
        # 2026-08-21). Cislo se odvozuje z hashe jmena, aby bylo
        # stabilni napric opakovanymi dotazy stejneho jmena.
        if name == "api-bs.gigaset-elements.de" or name.endswith(".gigaset-elements.de"):
            return target_ip
        if name not in resolved:
            offset = (zlib.crc32(name.encode()) % 200) + 10
            resolved[name] = f"{prefix}.{offset}"
        return resolved[name]

    def answer(packet: Any) -> None:
        if (
            DNS not in packet
            or Ether not in packet
            or IP not in packet
            or UDP not in packet
            or packet[Ether].src.lower() != base_mac.lower()
            or int(packet[DNS].qr) != 0
            or packet[DNS].qd is None
        ):
            return
        query = packet[DNS]
        name = bytes(query.qd.qname).decode("ascii", errors="replace").rstrip(".")
        if int(query.qd.qtype) != 1:
            return
        answer_ip = resolve(name)
        response = (
            Ether(src=iface.mac, dst=packet[Ether].src)
            / IP(src=packet[IP].dst, dst=packet[IP].src)
            / UDP(sport=53, dport=packet[UDP].sport)
            / DNS(
                id=query.id,
                qr=1,
                aa=1,
                rd=query.rd,
                ra=1,
                qd=query.qd,
                ancount=1,
                an=DNSRR(rrname=query.qd.qname, type="A", ttl=10, rdata=answer_ip),
            )
        )
        sendp(response, iface=iface, verbose=False)
        print(f"DNS {name} -> {answer_ip}", flush=True)

    sniff(iface=iface, filter="udp dst port 53", prn=answer, store=False)


def dns_server_loop(bind_ip: str, target_ip: str) -> None:
    """Answer DNS A queries so Windows does not reply ICMP port unreachable.

    The base station treats the ICMP error as a hard DNS failure and discards
    the spoofed answer, so a real listener on UDP 53 is required whenever the
    ICS DNS proxy is not running.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_ip, 53))
    print(f"DNS server poslouchá na {bind_ip}:53 -> {target_ip}", flush=True)
    answer_ip = socket.inet_aton(target_ip)
    while True:
        try:
            query, peer = sock.recvfrom(512)
        except OSError:
            continue
        if len(query) < 12:
            continue
        qdcount = int.from_bytes(query[4:6], "big")
        if qdcount != 1 or (query[2] & 0x80):
            continue
        offset = 12
        while offset < len(query) and query[offset]:
            offset += query[offset] + 1
        offset += 1
        if offset + 4 > len(query):
            continue
        name = query[12:offset]
        qtype = int.from_bytes(query[offset : offset + 2], "big")
        qclass = int.from_bytes(query[offset + 2 : offset + 4], "big")
        question = query[12 : offset + 4]
        labels = []
        pos = 0
        while pos < len(name) and name[pos]:
            length = name[pos]
            labels.append(name[pos + 1 : pos + 1 + length].decode("ascii", "replace"))
            pos += length + 1
        hostname = ".".join(labels)
        if qtype != 1 or qclass != 1:
            header = query[0:2] + b"\x81\x83" + query[4:6] + b"\x00\x00\x00\x00\x00\x00"
            sock.sendto(header + question, peer)
            continue
        header = query[0:2] + b"\x81\x80" + b"\x00\x01\x00\x01\x00\x00\x00\x00"
        record = b"\xc0\x0c" + b"\x00\x01\x00\x01" + (10).to_bytes(4, "big")
        record += (4).to_bytes(2, "big") + answer_ip
        sock.sendto(header + question + record, peer)
        print(f"DNS/UDP {peer[0]} {hostname} -> {target_ip}", flush=True)


CONTROL_UPLOAD_LIMIT = 256 * 1024


def _read_upload(conn: socket.socket, head: bytes, rest: bytes) -> bytes:
    """Docist telo POSTu podle Content-Length, nejvyse do pevneho stropu."""
    length = CONTROL_UPLOAD_LIMIT
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            try:
                length = min(int(value.strip()), CONTROL_UPLOAD_LIMIT)
            except ValueError:
                pass
    body = rest
    while len(body) < length:
        chunk = conn.recv(4096)
        if not chunk:
            break
        body += chunk
    return body[:length]


def control_poll_handler(conn: socket.socket, peer: str, gateway: "Gateway") -> None:
    """Obslouzit jeden dotaz gwctl. Prosty HTTP - busybox wget TLS neumi."""
    try:
        conn.settimeout(5)
        request = b""
        while b"\r\n\r\n" not in request and len(request) < 4096:
            chunk = conn.recv(1024)
            if not chunk:
                break
            request += chunk
        line = request.split(b"\r\n", 1)[0].decode("ascii", "replace")
        parts = line.split(" ")
        if not line.startswith("GET /gwctl"):
            # Dotazy na prikazy chodi kazdych par sekund, ostatni jsou vzacne a
            # stoji za zaznam - jinak nejde poznat, jestli vubec dorazily.
            print(f"CONTROL POŽADAVEK {peer} {line[:120]}", flush=True)
        # Odmitnuti cizich peeru na /gwctl. Puvodne to overovalo clenstvi v
        # base_ids, ale ten se plni jen prilezitostne (z certifikatu nebo z
        # hlaseni pravidla) - u vic zakladen se tak libovolna z nich mohla
        # jevit jako "cizi" jen proto, ze tam jeste nic takoveho neprislo, a
        # jeji /gwctl byl trvale odmitany. Manifest ma naproti tomu kazda
        # nakonfigurovana zakladna hned od startu (jinak by se brana vubec
        # nespustila), takze je to spolehlivy test "je tohle vubec nekym, s
        # kym pocitame".
        known = gateway._manifest_path_for(peer) is None
        upload = (
            len(parts) >= 2
            and parts[0] == "POST"
            and parts[1].split("?", 1)[0] in {"/manifest", "/inventory"}
        )
        if upload:
            if known:
                body = b""
                status = b"403 Forbidden"
                print(f"CONTROL UPLOAD odmítnut {peer}", flush=True)
            else:
                head, _, rest = request.partition(b"\r\n\r\n")
                document = _read_upload(conn, head, rest)
                if parts[1].startswith("/inventory"):
                    accepted = gateway.store_base_inventory(document, peer)
                else:
                    accepted = gateway.store_base_manifest(document, peer)
                body = b""
                status = b"200 OK" if accepted else b"400 Bad Request"
        elif len(parts) < 2 or parts[0] != "GET":
            body = b""
            status = b"400 Bad Request"
        elif known:
            # Fronta se cte destruktivne, takze ji smi vybrat jen zakladna.
            body = b""
            status = b"403 Forbidden"
            print(f"CONTROL POLL odmítnut {peer}", flush=True)
        else:
            _, _, query = parts[1].partition("?")
            if query.startswith("s="):
                print(f"CONTROL STAV {peer} {query[2:]}", flush=True)
            body = gateway.take_control_poll_lines(peer).encode("ascii", "replace")
            status = b"200 OK"
            gateway.note_control_poll(peer)
        conn.sendall(
            b"HTTP/1.1 " + status + b"\r\nContent-Type: text/plain\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"Connection: close\r\n\r\n"
            + body
        )
    except OSError:
        pass
    finally:
        conn.close()


def control_poll_loop(bind_ip: str, port: int, gateway: "Gateway") -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((bind_ip, port))
    server.listen(5)
    print(f"Řídicí kanál pro gwctl na http://{bind_ip}:{port}/", flush=True)
    while True:
        conn, peer = server.accept()
        threading.Thread(
            target=control_poll_handler,
            args=(conn, peer[0], gateway),
            daemon=True,
        ).start()


def ntp_response_payload(request: bytes) -> bytes:
    epoch_offset = 2208988800
    unix_time = time.time()
    seconds = int(unix_time) + epoch_offset
    fraction = int((unix_time - int(unix_time)) * (1 << 32))
    now = seconds.to_bytes(4, "big") + fraction.to_bytes(4, "big")
    response = bytearray(48)
    response[0] = 0x24  # LI=0, VN=4, Mode=4 (server)
    response[1] = 1  # stratum 1
    response[2] = request[2]  # echo the client's poll interval
    response[3] = 0xEC  # precision 2^-20 s
    response[12:16] = b"LOCL"
    response[16:24] = now  # reference timestamp
    response[24:32] = request[40:48]  # originate = client transmit timestamp
    response[32:40] = now  # receive timestamp
    response[40:48] = now  # transmit timestamp
    return bytes(response)


def raw_ntp_loop(interface: str, base_mac: str) -> None:
    """Answer the base station's NTP requests with scapy.

    /usr/sbin/ntpd_daemon.sh synchronises against ntp.gigaset-elements.com and
    the pool.ntp.org peers before the base station finishes booting.  Without a
    reachable time source the box keeps its 2022 default clock and reboots in a
    loop.  UDP 123 is normally taken by the Windows Time service, so the reply
    is injected at layer 2 instead of binding a socket.
    """
    try:
        from scapy.all import Ether, IP, Raw, UDP, conf, sendp, sniff
    except ImportError as exc:
        raise RuntimeError("Raw NTP vyžaduje balíček scapy a Npcap.") from exc

    iface = conf.ifaces.dev_from_name(interface)

    def answer(packet: Any) -> None:
        if (
            Ether not in packet
            or IP not in packet
            or UDP not in packet
            or packet[Ether].src.lower() != base_mac.lower()
        ):
            return
        # scapy binds its own NTP dissector to port 123, so the payload is not
        # exposed as a Raw layer.
        request = bytes(packet[UDP].payload)
        if len(request) < 48 or (request[0] & 0x07) != 3:  # mode 3 = client
            return
        response = (
            Ether(src=iface.mac, dst=packet[Ether].src)
            / IP(src=packet[IP].dst, dst=packet[IP].src)
            / UDP(sport=123, dport=packet[UDP].sport)
            / Raw(load=ntp_response_payload(request))
        )
        sendp(response, iface=iface, verbose=False)
        print(f"NTP {packet[IP].src} -> {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    sniff(iface=iface, filter="udp dst port 123", prn=answer, store=False)


def serve(config: dict[str, Any]) -> None:
    gateway = Gateway(config)
    bind_ip = config.get("bind_ip", "0.0.0.0")
    port = int(config.get("port", 443))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # Zadny strop na verzi tu drive nebyl, takze se s klientem, ktery umi
    # TLS 1.3, vyjednavalo 1.3. Podezreni (2026-08-21, druha zakladna stejne
    # znacky/firmwaru): jeji TLS 1.3 cesta certifikat validuje prisneji nez
    # TLS 1.2 (odmitne self-signed s "unknown_ca" behem ~20 ms po prijeti,
    # i kdyz je certifikat sam o sobe v poradku - overeno rucne). Zustat na
    # 1.2 - presne to, na co uz mirilo puvodni minimum_version.
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers("ALL:@SECLEVEL=0")
    context.load_cert_chain(config["certificate"], config["private_key"])

    control = config.get("control", {})
    if control.get("enabled"):
        threading.Thread(
            target=control_poll_loop,
            args=(
                bind_ip,
                int(control.get("poll_port", CONTROL_POLL_PORT)),
                gateway,
            ),
            daemon=True,
        ).start()

    if config.get("dns_server", True):
        threading.Thread(
            target=dns_server_loop,
            args=(bind_ip, config.get("dns_target_ip", bind_ip)),
            daemon=True,
        ).start()

    raw_dns = config.get("raw_dns", {})

    if config.get("ntp_server", True) and raw_dns.get("enabled", False):
        threading.Thread(
            target=raw_ntp_loop,
            args=(raw_dns["interface"], raw_dns["base_mac"]),
            daemon=True,
        ).start()

    if raw_dns.get("enabled", False):
        threading.Thread(
            target=raw_dns_loop,
            args=(raw_dns["interface"], raw_dns["base_mac"], raw_dns["target_ip"]),
            daemon=True,
        ).start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((bind_ip, port))
    server.listen(20)
    print(f"Gigaset gateway poslouchá na https://{bind_ip}:{port}", flush=True)
    while True:
        raw, peer = server.accept()
        threading.Thread(
            target=handle_connection,
            args=(raw, peer, context, gateway),
            daemon=True,
        ).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Lokální gateway pro Gigaset Elements Base")
    parser.add_argument("--config", default="gigaset_gateway.json")
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    for key in (
        "certificate",
        "private_key",
        "event_log",
        "state_file",
        "command_state_file",
        "command_queue_file",
    ):
        if key in config and not Path(config[key]).is_absolute():
            config[key] = str(config_path.parent / config[key])
    serve(config)


if __name__ == "__main__":
    main()
