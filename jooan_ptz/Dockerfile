FROM ghcr.io/home-assistant/base:3.22

ARG BUILD_VERSION
ARG BUILD_ARCH

RUN apk add --no-cache python3 py3-pip ffmpeg

WORKDIR /app
COPY app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}" \
  io.hass.name="JOOAN Local Control" \
  io.hass.description="Local-only LAN control for compatible JOOAN/CAM720 cameras" \
  org.opencontainers.image.source="https://github.com/lucaslucian/ha-jooan-ptz"

CMD ["/run.sh"]
