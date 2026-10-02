# Changelog

## 0.5.0

- Added a low-impact background heartbeat that only opens TCP/80 to determine whether the camera is online.
- Full CGI, port 9898, ONVIF and RTSP discovery now runs at startup and only when a user explicitly requests a diagnostic refresh.
- Paused browser status polling and media refresh while the Home Assistant App tab/page is hidden.
- Media snapshots refresh only while the page is visible and the media card is on screen.
- Added read-only ONVIF diagnostics for device information, system date/time, network interfaces, scopes and service list.
- Added ONVIF video-source discovery.
- Added PTZ node and PTZ configuration discovery, in addition to status and presets.
- Kept all new diagnostics read-only and pinned to the configured private camera IP.


## 0.4.1

- Fixed snapshots being recreated every 5 seconds by the status polling loop.
- Changed snapshot refresh to sequential requests so the stock JA-A12 is not hit by several FFmpeg/RTSP sessions at once.
- Preserved the last successfully displayed frame when a later snapshot request fails.
- Added backend serialization for snapshot capture as a second layer of protection against concurrent browser requests.
- Added an in-memory last-good-frame fallback, keyed by camera IP, RTSP port and stream.
- Prefer the main `*_0` stream for each channel in the media panel, reducing duplicate main/substream load.
- Kept manual snapshot refresh available without replacing a good image with a broken one on transient errors.


## 0.4.0

- Confirmed ONVIF TCP/8899 and `/onvif/device_service` on the stock JA-A12 test unit.
- Expanded read-only ONVIF discovery to `GetCapabilities`, `GetProfiles`, `GetStreamUri`, `GetStatus` and `GetPresets`.
- Sanitized ONVIF XAddr and RTSP URI data; reported hosts are never followed by the App.
- Kept all ONVIF discovery pinned to the configured private camera IP.
- Changed RTSP probing from four concurrent ffprobe processes to sequential probing for the constrained embedded RTSP server.
- Prioritized the previously confirmed main paths `/live/ch00_0` and `/live/ch01_0`.
- Added ONVIF-discovered RTSP paths as safe probe candidates.
- Added parser and sequential-probe regression tests.
- Updated compatibility and local protocol documentation with the first stock hardware results.


## 0.3.2

- Fixed repeated HTTP health checks leaking persistent camera connections.
- Switched CGI authentication validation to the confirmed PTZ `stop` command.
- Treated `getPlatformID` as optional device information instead of the sole authentication check.
- Added redaction for secrets embedded inside network exception messages.
- Prevented timeout errors from leaking `userkey` or RTSP credentials into logs/UI.
- Added regression tests for secret redaction and authentication validation.


## 0.3.1

- Added Home Assistant `icon.png` (128x128).
- Added Home Assistant `logo.png` (250x100).
- Added an app-level `README.md` for the App Store introduction.
- Expanded `DOCS.md` with setup, diagnostics, security and troubleshooting.
- Added English and Brazilian Portuguese option translations.
- Improved App Store description and panel title.
- Added compatibility and UI design documentation.
- Added the approved full-dashboard visual concept to the repository documentation.
- Added CI validation for presentation files, translations and image formats.

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
