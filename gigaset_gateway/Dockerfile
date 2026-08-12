# Alpine base z Home Assistantu.  BUILD_FROM ani build.yaml uz Supervisor
# od verze 2026.04 nepouziva, obraz se musi urcit primo.
FROM ghcr.io/home-assistant/base:latest

# py3-cryptography a py3-paho-mqtt jsou v Alpine jako hotove balicky, takze se
# na malych ARM zarizenich nic nekompiluje.
RUN apk add --no-cache python3 py3-cryptography py3-paho-mqtt

COPY gigaset_gateway.py generate_certificate.py cre_manifest.json /opt/gigaset/
COPY cre/ /opt/gigaset/cre/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
