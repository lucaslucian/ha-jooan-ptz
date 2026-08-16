ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip

WORKDIR /app
COPY app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh

LABEL \
  io.hass.type="app" \
  io.hass.name="JOOAN PTZ" \
  io.hass.description="Local LAN PTZ control for JOOAN cameras" \
  org.opencontainers.image.source="https://github.com/lucaslucian/ha-jooan-ptz"

CMD ["/run.sh"]
