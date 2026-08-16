# JOOAN PTZ for Home Assistant

Local LAN PTZ control for compatible JOOAN cameras.

## Current version

**0.1.0**

The first version focuses on commands already confirmed during reverse engineering:

- Up
- Down
- Left
- Right
- Stop

Authentication follows the camera's local CGI protocol:

```text
userkey = MD5(camera_password)
```

Requests are sent directly to:

```text
http://CAMERA_IP/goform/SingleHandlebyCommand
```

No JOOAN cloud service, MQTT broker, or Internet connection is required for PTZ control.

## Installation

Add this repository to Home Assistant's Add-on Store:

```text
https://github.com/lucaslucian/ha-jooan-ptz
```

Install **JOOAN PTZ**, configure the camera IP and password, then start the add-on.

The first release exposes a small web control panel through Home Assistant Ingress. A native Home Assistant integration/card will be added after the local API is validated on real Home Assistant OS installations.

## Development roadmap

- Validate the add-on on Home Assistant OS.
- Improve camera discovery/validation.
- Expose camera information and network state.
- Investigate additional local CGI commands.
- Add native Home Assistant entities/services and a Lovelace PTZ card.
- Investigate locally available home/reset and other PTZ features.
