# Changelog

## 0.3.0

- Added read-only LAN capability discovery via `getAPLanP2PSupport`.
- Added safe/allowlisted parsing of port 9898 device properties.
- Added local state/capability diagnostics for SD, recording, motion, tracking and lights.
- Added TCP service probes for HTTP, RTSP, feature service and ONVIF.
- Added read-only ONVIF `GetCapabilities` probe on configurable port 8899.
- Added FFmpeg/ffprobe-based RTSP stream detection.
- Added snapshot generation for allowlisted RTSP paths.
- Added snapshot/media diagnostics to the Ingress panel.
- Added explicit filtering so unknown properties and camera secrets are not exposed to the UI.
- Added protocol safety tests including rejection of `SetDiagMode`.
- Added comprehensive `docs/LOCAL_PROTOCOL.md` research notes.

## 0.2.0

- Renamed to JOOAN Local Control.
- Updated build layout for Supervisor 2026.04+.
- Removed dependency on legacy `BUILD_FROM`.
- Limited supported architectures to `amd64` and `aarch64`.
- Migrated the web server to Gunicorn.
- Added Supervisor watchdog.
- Removed direct host publication of the web port; UI uses Ingress.
- Added public-Internet destination blocking.
- Ported device information from `joan_camcontrol`.
- Added port 9898 device-feature query.
- Added experimental dual-lens detection.
- Added local RTSP configuration confirmation.
- Improved PTZ so `stop` is sent when a direction button is released.
- Kept camera password and `userkey` out of logs.

## 0.1.2

- Added camera credential validation on startup.
- Added read-only `getPlatformID` validation.
- Added camera information display.
- Added network state display.
- Added periodic camera health checks.
- Kept passwords and derived authentication keys out of logs.

## 0.1.1

- Added configurable camera username.
- Added password-protected camera password option.
- Added local PTZ web control panel.

## 0.1.0

- Initial JOOAN local LAN PTZ add-on.
- Added up, down, left, right and stop commands.
