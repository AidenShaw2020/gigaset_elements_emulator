#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

DATA=/data
SHARE=/share/gigaset
CONFIG="${DATA}/gigaset_gateway.json"
CERTIFICATE="${DATA}/lab.cert.pem"
PRIVATE_KEY="${DATA}/lab.key.pem"
MANIFEST="${DATA}/cre_manifest.json"
GENERATED_CRE="${DATA}/cre"
BASE_CONFIGURATION="${DATA}/base_configuration.json"
CONTROL_REQUESTS="${SHARE}/control.json"

mkdir -p "${GENERATED_CRE}" "${SHARE}"

# bashio::config vraci u nevyplnene volby retezec "null", ktery je pro
# bashio::var.is_empty neprazdny - bez has_value by se z nej slozila adresa
# ridiciho kanalu "http://null:8080/gwctl" a zakladna by si prikazy nemela kde
# vyzvednout.
if bashio::config.has_value 'gateway_address'; then
    GATEWAY_ADDRESS=$(bashio::config 'gateway_address')
else
    # Brana si adresu vezme z prvniho spojeni od zakladny - je to presne ta,
    # na kterou se dovolala, i kdyz provoz prochazi pres DNAT.
    GATEWAY_ADDRESS=""
    bashio::log.info "Adresa brány se zjistí z prvního spojení od základny."
fi

FIRMWARE_CRE=$(bashio::config 'firmware_cre_dir')
mkdir -p "${FIRMWARE_CRE}"
# Zakladna si stahuje jen ty polozky manifestu, kterym se zmenil nazev souboru.
# Puvodni knihovny uz na sobe ma z doby, kdy ji provisionoval originalni cloud,
# takze tenhle adresar je zaloha pro pripad tovarniho resetu - ne podminka behu.
if [ -z "$(ls -A "${FIRMWARE_CRE}" 2>/dev/null)" ]; then
    bashio::log.warning \
        "V '${FIRMWARE_CRE}' nejsou žádné Lua soubory z firmwaru. Pro běžný
         provoz to nevadí, ale po továrním resetu základny je budete potřebovat."
fi

# --- MQTT -------------------------------------------------------------------
# Bez vyplneneho hostitele se pouzije broker, ktery Home Assistant nabizi jako
# sluzbu; doplnovat udaje rucne je tedy potreba jen u externiho brokeru.
MQTT_HOST=$(bashio::config 'mqtt_host')
MQTT_PORT=$(bashio::config 'mqtt_port')
MQTT_USERNAME=$(bashio::config 'mqtt_username')
MQTT_PASSWORD=$(bashio::config 'mqtt_password')

if bashio::var.is_empty "${MQTT_HOST}"; then
    if ! bashio::services.available 'mqtt'; then
        bashio::exit.nok \
            "Není nastaven 'mqtt_host' a v Home Assistantu neběží MQTT broker."
    fi
    MQTT_HOST=$(bashio::services 'mqtt' 'host')
    MQTT_PORT=$(bashio::services 'mqtt' 'port')
    MQTT_USERNAME=$(bashio::services 'mqtt' 'username')
    MQTT_PASSWORD=$(bashio::services 'mqtt' 'password')
    bashio::log.info "MQTT z Home Assistantu: ${MQTT_HOST}:${MQTT_PORT}"
fi

# --- certifikat -------------------------------------------------------------
# Zakladna retez neoveruje, staci self-signed.  Generuje se jen jednou, aby si
# po restartu doplnku nemusela znovu vyzvedavat podpis.
if [ ! -f "${CERTIFICATE}" ] || [ ! -f "${PRIVATE_KEY}" ]; then
    CERT_ARGS=""
    for host in $(bashio::config 'certificate_hostnames'); do
        CERT_ARGS="${CERT_ARGS} --dns ${host}"
    done
    for address in $(bashio::config 'certificate_addresses'); do
        CERT_ARGS="${CERT_ARGS} --ip ${address}"
    done
    # shellcheck disable=SC2086
    python3 /opt/gigaset/generate_certificate.py \
        ${CERT_ARGS} \
        --cert "${CERTIFICATE}" --key "${PRIVATE_KEY}"
    bashio::log.info "Vytvořen certifikát ${CERTIFICATE}"
fi

# --- soubory, ktere si brana za behu prepisuje ------------------------------
# Manifest rika zakladne, ktere Lua ma nacist, a jmenuje presne ty verze, ktere
# ji kdysi nadelil originalni cloud - vcetne pravidel zalozenych uzivatelem.
# Kazda zakladna ma proto svuj vlastni; ten zabudovany pochazi z vyvojove a na
# cizi zakladne by jmenoval soubory, ktere nikdy nemela.
SOURCE_MANIFEST=/opt/gigaset/cre_manifest.json
if [ -f "${SHARE}/cre_manifest.json" ]; then
    SOURCE_MANIFEST="${SHARE}/cre_manifest.json"
    bashio::log.info "Manifest CRE z '${SOURCE_MANIFEST}'."
else
    bashio::log.warning \
        "Používá se zabudovaný manifest CRE. Pokud vaše základna hlásí chybějící
         soubory, zkopírujte z ní '/cfg/cre' do '${SHARE}/cre_manifest.json'."
fi

# Vlastni knihovny doplnku se do manifestu doplni vzdy, at uz pochazi odkudkoliv.
# Nazev souboru je <klic>-<verze>.lua, takze klic staci uriznout.
OWN_LIBRARIES=$(
    for path in /opt/gigaset/cre/*.lua; do
        [ -e "${path}" ] || continue
        file=$(basename "${path}")
        key=${file%-*}
        jq -n --arg key "${key}" --arg file "${file}" \
            '{($key): "/api/v1/bs01/configuration/libs/\($key)/\($file)"}'
    done | jq -s 'add // {}'
) || OWN_LIBRARIES=""
[ -n "${OWN_LIBRARIES}" ] || OWN_LIBRARIES="{}"

# Serie gwctl musi prezit restart, jinak by zakladna delala zbytecny reload CRE.
[ -f "${MANIFEST}" ] || echo '{}' > "${MANIFEST}"
if jq -s --argjson own "${OWN_LIBRARIES}" '
    .[0] as $source
  | .[1] as $current
  | $source
  | .endnode_libraries = ((.endnode_libraries // {}) + $own)
  | if ($current.endnode_libraries.gwctl // "") == "" then .
    else .endnode_libraries.gwctl = $current.endnode_libraries.gwctl end
' "${SOURCE_MANIFEST}" "${MANIFEST}" > "${MANIFEST}.new"; then
    mv "${MANIFEST}.new" "${MANIFEST}"
else
    rm -f "${MANIFEST}.new"
    bashio::log.warning "Manifest CRE nelze sloučit, používá se ten v /data."
fi
if [ ! -f "${CONTROL_REQUESTS}" ]; then
    echo '{ "requests": [] }' > "${CONTROL_REQUESTS}"
fi

# Zakladna soubory, ktere v manifestu nejsou, ze sveho disku SMAZE (overeno
# 2026-08-14).  Poslat ji necely manifest proto znamena nenavratne prijit o
# pravidla, ktera uz z vypnuteho cloudu nikdo neziská - nikdy to nedelat
# automaticky.  Kdyz jeji manifest nezname, je jedina bezpecna cesta nechat si
# ho dodat od uzivatele.
BOOTSTRAP=false

jq -n \
    --arg timezone "$(bashio::config 'timezone')" \
    --arg timezone_name "$(bashio::config 'timezone_name')" \
    '{timezone: $timezone, timezoneName: $timezone_name, last_intrusion_mode: "home"}' \
    > "${BASE_CONFIGURATION}"

# --- konfigurace brany ------------------------------------------------------
jq -n \
    --argjson options "$(cat /data/options.json)" \
    --arg gateway "${GATEWAY_ADDRESS}" \
    --arg certificate "${CERTIFICATE}" \
    --arg private_key "${PRIVATE_KEY}" \
    --arg manifest "${MANIFEST}" \
    --arg generated_cre "${GENERATED_CRE}" \
    --arg firmware_cre "${FIRMWARE_CRE}" \
    --arg base_configuration "${BASE_CONFIGURATION}" \
    --arg base_manifest "${SHARE}/cre_manifest.json" \
    --arg bootstrap "${BOOTSTRAP}" \
    --arg control_requests "${CONTROL_REQUESTS}" \
    --arg mqtt_host "${MQTT_HOST}" \
    --arg mqtt_port "${MQTT_PORT}" \
    --arg mqtt_username "${MQTT_USERNAME}" \
    --arg mqtt_password "${MQTT_PASSWORD}" \
    '{
        bind_ip: "0.0.0.0",
        port: $options.port,
        base_id: ($options.base_id // ""),
        dns_server: false,
        ntp_server: false,
        log_headers: $options.log_requests,
        event_stream_seconds: 1,
        event_keepalive_seconds: 2,
        cloud_heartbeat_tick: 60,
        socket_timeout_seconds: 10,
        certificate: $certificate,
        private_key: $private_key,
        event_log: "/data/gigaset_events.jsonl",
        state_file: "/data/gigaset_state.json",
        command_state_file: "/data/gigaset_command_state.json",
        command_queue_file: "/data/gigaset_commands.json",
        diagnostic_dir: "/data/diagnostic",
        event_commands: [],
        raw_dns: { enabled: false },
        base_configuration_file: $base_configuration,
        cre_manifest_file: $manifest,
        base_manifest_file: $base_manifest,
        bootstrap_manifest: ($bootstrap == "true"),
        cre_source_dirs: [ $generated_cre, "/opt/gigaset/cre", $firmware_cre ],
        control: {
            enabled: true,
            request_file: $control_requests,
            library_dir: $generated_cre,
            library: "gwctl",
            serial: 1,
            poll_url: (if $gateway == "" then ""
                       else "http://" + $gateway + ":"
                            + ($options.control_poll_port | tostring)
                            + "/gwctl" end),
            poll_port: $options.control_poll_port,
            poll_interval: $options.control_poll_interval
        },
        mqtt: {
            enabled: true,
            host: $mqtt_host,
            port: ($mqtt_port | tonumber),
            username: $mqtt_username,
            password: $mqtt_password,
            client_id: "gigaset-elements-gateway",
            base_topic: $options.mqtt_base_topic,
            discovery_prefix: $options.mqtt_discovery_prefix,
            motion_off_delay: $options.motion_off_delay,
            battery_empty_mv: $options.battery_empty_mv,
            battery_full_mv: $options.battery_full_mv
        }
    }' > "${CONFIG}"

bashio::log.info "Cloudové API na portu $(bashio::config 'port'), řídící kanál na $(bashio::config 'control_poll_port')"
exec python3 -u /opt/gigaset/gigaset_gateway.py --config "${CONFIG}"
