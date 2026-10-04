# Changelog

## 0.18.0

- Removed the **Laboratory** feature completely from the App UI and backend API. The `/api/lab/*` routes, SetDiagMode callback port/configuration and laboratory test suite were removed.
- Replaced the tabbed interface with a single organized dashboard page containing: overview, dual-lens media/PTZ, detection, recording, diagnostics and all read camera settings.
- Startup validation is now intentionally lightweight: only the proven CGI/OEM reads run automatically. ONVIF discovery is manual and RTSP stream probing is never part of startup.
- Deep diagnostics no longer fan out into `ffprobe` sessions. This change is intended to reduce pressure on the camera after repeated observations of instability/offline behavior around media probing.
- Snapshots and live RTSP previews are fully **on demand**. No media session is opened automatically when the panel loads.
- Added per-lens Snapshot / Live / Stop controls. Starting both live feeds negotiates the first feed before opening the second to avoid simultaneous RTSP handshakes.
- ONVIF PTZ remains available as a production feature after manual ONVIF discovery, with CGI PTZ as the automatic fallback.
- Moved the bounded ONVIF ContinuousMove implementation from the removed laboratory module into the production probe module.
- Removed the laboratory-only port `49000` and `diag_callback_ip` option.


## 0.17.0

- Rebuilt the main **Câmeras & PTZ** workspace around the validated local-control use case instead of experimental camera configuration.
- Added two simultaneous live panels for the dual-lens camera, preferring the low-resolution `ch00_1` and `ch01_1` RTSP substreams and falling back to the corresponding main stream when necessary.
- Live previews now have independent per-channel FFmpeg processes and lifecycle state, so one feed can stop or fail without automatically terminating the other.
- Increased Gunicorn from 4 to 6 threads so two long-lived MJPEG responses still leave capacity for status polling and PTZ requests.
- Increased the MJPEG startup preflight window from 4 to 10 seconds to tolerate embedded-camera keyframe/startup delay.
- Promoted PTZ to an **ONVIF-first** control path with selectable normalized speed; if ONVIF `ContinuousMove` is rejected or fails, the backend automatically falls back to the already validated CGI PTZ commands.
- Added a camera information panel beside PTZ with model, firmware, network/media status, channels, codec and timezone.
- Added a large **Todas as configurações lidas** panel that aggregates only values actually reported by the camera through OEM state, safe properties, capabilities, platform/network data, RTSP and ONVIF.
- Configuration writers remain experimental and are not promoted into the main dashboard.


## 0.16.2

- Added an explicit **forced legacy CGI write** mode in the Laboratory for one final bounded hardware test when the paired read endpoint returns no usable values or is absent.
- Forced mode still uses the fixed allowlist: it never accepts an arbitrary CGI path, parameter name or value.
- When readback is insufficient, forced mode sends **only the selected allowlisted field** instead of guessing sibling settings; automatic rollback is therefore not claimed.
- Candidate writer HTTP errors such as 404 are now returned as structured results and stop verification immediately, avoiding extra requests to the fragile camera service.
- Normal mode is unchanged and still requires enough reader state to preserve existing fields.
- Firmware update, factory reset and Wi-Fi configuration remain excluded.


## 0.16.1

- Changed legacy CGI discovery to prefer the **POST-query** form used by the related GoAhead page and now observed working on the reference JA-A12.
- A clean **HTTP 404** now terminates the probe immediately; the laboratory no longer tries the same missing endpoint with another HTTP method.
- A timeout/network failure also terminates that surface probe instead of immediately consuming another embedded-camera HTTP worker.
- Missing CGI surfaces are cached for the lifetime of the App process so repeated laboratory clicks do not keep hitting a known-absent route.
- Added raw-status support to the fixed GET/POST-query backend helpers so the lab can distinguish 404/405/501 without converting every expected probe result into a generic network error.
- Hardware result recorded: `getVideoSettings` POST-query returned HTTP 200; `getmotiondetectSettings` returned HTTP 404 on both tested methods.
- Improved the laboratory summary so HTTP 404 is shown as an absent endpoint and HTTP 200 without recognized configuration fields does not falsely validate a writer.

## 0.16.0

- Added an experimental **legacy GoAhead CGI** laboratory based on public related-camera firmware/API evidence.
- Added read-only probes for `/goform/getVideoSettings` and `/goform/getmotiondetectSettings`, trying GET first and the original GoAhead POST-query form when needed, returning only allowlisted parsed fields and never raw response bodies.
- Added guarded no-op round trips for `/goform/updateVideoSettings` and `/goform/updatemotiondetectSettings`: the backend first reads current values, replays only those values, then compares both CGI and port-9898 OEM readback.
- Added explicit candidate writers for motion enable, 25-zone mask, legacy sensitivity 0..5, image rotation/Flip, IR mode, 50/60 Hz flicker and the documented 0..100 day/night brightness/contrast/saturation fields. All paths, parameter names, ranges and enums are fixed allowlists.
- Added a separately guarded `/goform/NTP` timezone candidate with a closed list of legacy timezone values and port-9898 timezone readback.
- Improved laboratory request locking so all controls are disabled immediately while one camera-I/O test is running.
- Added regression coverage for safe legacy parsing, read probes, no-op round trips, state-preserving writes, arbitrary-parameter rejection and NTP allowlisting.
- Updated documentation with confirmed JA-A12 mappings: `autotrack 0/1`, `flipmirror 0/3`, `floodlight 0/1/2/3`, motion sensitivity `1/2/3`, 25-bit motion zones and OEM `GMT±HH:MM` timezone format.
- Replaced the low-resolution 320×180 dashboard concept JPEG with a 1600×900 vector SVG, added a 1000×400 vector project wordmark, and updated CI to validate both vector assets.
- Refreshed README, compatibility, UI-design and App documentation to match the current laboratory and hardware findings.
- Firmware update, factory reset, Wi-Fi configuration, arbitrary CGI/singleCMD/SOAP/DP/MQTT proxies and diagnostic shell execution remain excluded.

## 0.15.0

- Added a guarded **OEM write laboratory** for values already mapped on the JA-A12.
- Added strict write plans for `floodlight`, motion zones, motion sensitivity, `autotrack`, `flipmirror` and `timezone`.
- Motion ON/OFF plans use the confirmed paired 25-bit masks: `mdarea/sub_mdarea = 33554431` for all zones and `0` for no zones.
- The lighting plan exposes the confirmed `floodlight` enum: 0 infrared, 1 white LED, 2 smart detection light, 3 IR/night vision disabled.
- Flip Mirror plans map OFF/ON to the observed values 0/3; sensitivity maps low/medium/high to 1/2/3 on both main/sub fields.
- Added a fixed, read-only `GetJsonConf` probe for `SystemInfo/ProductName` plus 9898 readback to inspect the stock configuration surface without leaking the full config.
- No guessed configuration setter is executed. The Execute button stays locked until a stock local writer is evidenced.
- The laboratory continues to forbid arbitrary CGI/singleCMD, DP/MQTT and diagnostic-shell execution.


## 0.14.1

- Added a read-only OEM `system` mapper group for stock port 9898.
- The group includes `timezone`, `powerfrequency`, `video_standard_red`, `qualitymode`, `definition` and `resolution`.
- This provides a direct CAM720/OEM baseline-diff path for timezone research without relying on ONVIF.
- No timezone write setter is guessed; the OEM write path still needs to be identified from evidence.


## 0.14.0

- Added privacy-safe wide OEM property fingerprints to the toggle mapper.
- The mapper can now report property names that changed outside the selected allowlisted group, without returning their values.
- Keys that look like passwords, credentials, authentication material, tokens, Wi-Fi/SSID data or other secrets are omitted from the wide fingerprint map.
- This prevents silent misses when a CAM720 setting is stored in an OEM property that has not yet been added to a known group.
- Motion-area comparisons now decode `mdarea` and `sub_mdarea` as 25-bit masks, reporting before/after bit strings, changed bit indexes and active-zone counts.
- Hardware evidence so far: `33554431 = 0x1ffffff` represents all 25 motion zones enabled; clearing the two opposite corner zones produced `16777214 = 0x0fffffe`, clearing bits 24 and 0.
- Smart-detection sensitivity tests can now reveal an unknown outside-group property if the CAM720 stores that setting outside person_detect/vehicle_detect/pdarea.


## 0.13.1

- Clarified that the OEM recording mapper and OEM toggle mapper use independent baselines.
- Renamed baseline and compare buttons so the target is explicit: recording versus the selected toggle group.
- Recording compare now remains disabled until a recording baseline has been captured.
- Toggle compare now remains disabled until a baseline has been captured for the currently selected group.
- Changing the toggle group clears its baseline and disables comparison until a new baseline is captured.
- Fixed the generic laboratory availability refresh from unintentionally re-enabling compare buttons without a valid baseline.


## 0.13.0

- Simplified the Laboratory tab around features that remain useful on the validated JA-A12.
- Removed preset, Imaging write/path and ONVIF Recording Job/Replay controls from the Laboratory UI after hardware validation showed they are not usable on this firmware.
- Kept validated ONVIF PTZ, IR auxiliary commands, PullPoint Events, restricted SetDiagMode and the OEM recording mapper.
- Added a read-only OEM toggle mapper over the stock port-9898 `get_deviceFeatures` endpoint.
- Added allowlisted groups for motion, smart detection, tracking, lighting, alerts and privacy.
- The toggle mapper supports baseline -> change one option in CAM720 -> compare, showing exactly which OEM fields changed.
- No guessed OEM setter was added: public research did not provide a sufficiently reliable local write mapping for these JA-A12 properties.
- Target fields include motion detection/sensitivity, person/vehicle detection, automatic/person tracking, LED/floodlight/yellow light, push/audio/buzzer/siren, privacy/PTZ-hide and flip/mirror.
- Firmware, reset and Wi-Fi remain excluded.


## 0.12.0

- Marked ONVIF Imaging writes unsupported on the validated JA-A12 after both advertised Imaging paths accepted GetOptions but rejected GetImagingSettings, and SetImagingSettings also returned ActionNotSupported.
- Marked ONVIF preset creation unsupported on the validated JA-A12; GetPresets remains diagnostic-only.
- Confirmed ONVIF Recording objects/tracks are exposed, but GetRecordingJobs returns an empty list and GetReplayUri returns ActionNotSupported, so ONVIF storage is inventory-only on this firmware.
- Simplified the laboratory UI to stop emphasizing unsupported preset/imaging/recording-write paths.
- Added a read-only OEM recording snapshot against the stock port-9898 get_deviceFeatures endpoint.
- The OEM recording snapshot exposes only allowlisted recording/SD fields plus the known playback-fast-forward capability.
- Added a browser-side baseline/diff workflow: capture baseline, change one recording setting in CAM720, then compare exactly which local OEM fields changed.
- No OEM recording setter is guessed or exposed yet.


## 0.11.0

- Added read-only discovery of existing ONVIF recording jobs with GetRecordingJobs, GetRecordingJobConfiguration and GetRecordingJobState.
- Added a guarded 5-second recording pulse for an already configured job.
- The pulse runs only when exactly one job is returned and its current mode is explicitly Idle.
- It changes only SetRecordingJobMode Idle -> Active -> Idle, reads job state and recording information around the pulse, and restores Idle in a finally block.
- The pulse never creates/deletes recordings, tracks or jobs and never changes job configuration.
- If the job is already Active, missing, ambiguous or lacks a safe recording token, the test performs no write.
- GetReplayUri remains unsupported on the validated JA-A12, so successful recording can be verified via job/recording state even if ONVIF video playback remains unavailable.


## 0.10.2

- Added one final read-only ONVIF Imaging path probe before marking Imaging writes unsupported on the validated JA-A12.
- The probe compares the Imaging path derived from GetCapabilities with the exact ver20 Imaging namespace path returned by GetServices, because this firmware reports unusual cross-mapped service paths.
- On each distinct path it tests only GetServiceCapabilities, GetOptions and GetImagingSettings; it never writes image settings.
- Preset work is no longer a priority for the validated unit because GetPresets is empty and SetPreset is unsupported.
- Recording inventory remains usable, while GetReplayUri is confirmed unsupported on this firmware.


## 0.10.1

- Fixed the Recording/Search/Replay playback probe route failing with `name 'onvif_recording_playback_probe' is not defined`; the function now has an explicit import in `main.py`.
- Added a regression test ensuring the playback probe symbol is available to the Flask route.
- Preset UI now lists every preset returned by `GetPresets` and allows `GotoPreset` for the selected camera-returned token.
- Preset creation remains restricted to `HA_TEST`, and deletion remains restricted to `HA_TEST` only.
- Added regression coverage proving `GotoPreset` accepts a non-`HA_TEST` preset only when the token was freshly returned by the camera.


## 0.10.0

- Confirmed the validated JA-A12 rejects ONVIF `SetPreset` with `ActionNotSupported`; `GetPresets` remains usable and currently returns an empty list.
- Confirmed the same firmware rejects both `GetImagingSettings` and `SetImagingSettings`; `GetOptions` remains usable and advertises Brightness, ColorSaturation, Contrast and Sharpness ranges.
- Confirmed ONVIF Recording/Search/Replay are functional on the stock camera despite unusual service-path mappings reported by `GetServices`.
- The camera returns one recording token, `OnvifRecordingToken_1`, containing video, audio and metadata tracks.
- Replay capabilities report RTP/RTSP/TCP support.
- Added a read-only playback probe that refreshes recording tokens from the camera, pins the selected token to that fresh result, calls `GetRecordingInformation`, and requests `GetReplayUri`.
- The raw replay URI is never exposed to the browser; only a sanitized descriptor is returned and query contents are redacted.
- The replay URI is not opened in v0.10.0; actual server-side playback remains a separate validation step.
- Firmware, reset, Wi-Fi configuration, recording creation/deletion and arbitrary SOAP/CGI proxies remain excluded.


## 0.9.0

- Confirmed that the stock firmware repeats ONVIF motion property snapshots with `PropertyOperation=Initialized` across successive PullMessages calls.
- Event results now distinguish initial state, repeated duplicate snapshots and real per-topic state transitions so Home Assistant work will not treat every initialized snapshot as a new detection.
- Added unique message counts, duplicate counts, latest/initial states and transition summaries to the PullPoint laboratory.
- Kept raw event messages for diagnostics and added pull indexes to show which PullMessages response produced each item.
- Confirmed Imaging options for `Brightness`, `ColorSaturation`, `Contrast` and `Sharpness`, each in the camera-reported range 1–255.
- Added a guarded ONVIF `SetImagingSettings` experiment for only those four fields, one integer value at a time, requesting `ForcePersistence=false`.
- Imaging writes remain experimental because this firmware returns `ActionNotSupported` for `GetImagingSettings`, so there is no automatic readback/restore path.
- Firmware, reset and Wi-Fi operations remain excluded.


## 0.8.0

- Confirmed stock ONVIF PullPoint delivery of both `VideoSource/MotionAlarm` with `State=true` and `RuleEngine/CellMotionDetector/Motion` with `IsMotion=true`.
- Extended event listening to bounded 5/15/30 second windows with multiple short PullMessages calls, message counts and a derived motion observation flag.
- Preserve ONVIF event Source/Key/Data sections plus `UtcTime` and `PropertyOperation` when the camera provides them.
- Added hierarchical Imaging output and named Min/Max range extraction so GetOptions no longer loses the parent setting names.
- Added read-only discovery for ONVIF Recording, Search and Replay services advertised by GetServices.
- Recording discovery uses only read operations: GetServiceCapabilities/GetRecordings, GetRecordingSummary and GetReplayConfiguration.
- Firmware update, factory reset, Wi-Fi changes and arbitrary command proxies remain explicitly excluded.


## 0.7.0

- Added a dedicated **Laboratório** tab for explicit, manual protocol experiments; normal operation remains unchanged.
- Added bounded ONVIF `ContinuousMove` tests with speed/duration limits and an unconditional `Stop` attempt.
- Added allowlisted ONVIF `SendAuxiliaryCommand` testing for the IR commands actually advertised by the camera (`tt:Irlamp|On/Off`).
- Added guarded ONVIF preset tests: list, create the fixed `HA_TEST` preset, goto only a token read back from the camera, and delete only `HA_TEST`.
- Added read-only ONVIF Imaging discovery using `GetImagingSettings` and `GetOptions` before enabling any imaging setter.
- Added ONVIF Events discovery plus a short-lived PullPoint test intended to determine whether motion/person/vehicle events can later be exposed to Home Assistant without polling.
- Added an inventory of OEM state fields whose write command is still unknown instead of guessing setter endpoints.
- Added guarded `SetDiagMode` tests: force OFF, plus an active short-lived callback probe on fixed port 49000. The sink accepts only the configured camera, sends/reads no command payload, uses an ephemeral authorization code and forces OFF in a finally block.
- Explicitly excluded firmware update, factory reset and Wi-Fi configuration from the laboratory.
- Added tests for ONVIF movement stop guarantees, IR allowlisting, preset deletion boundaries, callback-host pinning and the fixed SetDiagMode disable request.

## 0.6.1

- Infer the PTZ-controlled lens from the ONVIF PTZ profile-to-RTSP mapping; on the validated JA-A12 data this maps `profile_0` to channel `ch00`.
- The Cameras & PTZ page now exposes only the PTZ lens as the continuous preview; both lenses remain available as snapshots below.
- Preview validation targets only the PTZ channel instead of probing both substreams.
- Made FFmpeg preview startup more conservative by removing aggressive low-latency probe flags that could prevent frames on the embedded RTSP server.
- Added MJPEG startup preflight: the backend only marks preview active after FFmpeg actually emits a multipart JPEG frame.
- If the preferred PTZ substream does not emit MJPEG, the backend closes it and automatically retries the confirmed PTZ main stream.
- Added safe preview failure state to diagnostics without exposing RTSP credentials.


## 0.6.0

- Rebuilt the Ingress panel into five user-facing sections: Overview, Cameras & PTZ, Detection, Recording and Diagnostics.
- Replaced raw JSON as the primary UI with status cards, service health, friendly capability/state labels, stream metadata, SD/recording summaries and schedule summaries.
- Kept full raw diagnostics available behind expandable technical details.
- Added an on-demand local MJPEG preview bridge using FFmpeg; RTSP credentials never reach the browser.
- Limited continuous video to one RTSP preview session at a time and automatically supersede the previous lens/session.
- Prefer validated/ONVIF-discovered substreams for preview and fall back to the confirmed main stream when needed.
- Added explicit manual substream validation for `/live/ch00_1` and `/live/ch01_1`.
- Kept PTZ available during live preview while blocking snapshots/deep diagnostics from opening competing camera sessions.
- Automatically starts live preview when the user begins PTZ from the camera tab, and stops preview when the page is hidden/unloaded.
- Added 640px / 6fps server-side MJPEG scaling for a practical intermediate preview on Raspberry Pi-class hosts.
- Added dedicated dashboard template/CSS/JavaScript files and CI JavaScript syntax validation.
- Added tests for preview selection, stream lifecycle, substream validation, dashboard serving and snapshot fallback during live preview.


## 0.5.2

- Serialized camera-facing CGI, port 9898, ONVIF and RTSP operations to prevent concurrent load on constrained firmware.
- Changed manual deep diagnostics to an asynchronous single-instance job so Gunicorn requests do not block until the entire probe finishes.
- Added a 5-minute in-memory cache for RTSP credentials, avoiding a RtspConf CGI request for every snapshot.
- Reduced RTSP discovery to main-stream-first per channel, with substreams and ONVIF paths used only as fallbacks, capped at four ffprobe sessions.
- Added short pacing between ONVIF SOAP calls and fail-fast behavior after transport failures or authentication gates.
- Hardened ONVIF numeric parsing against malformed firmware values and limited SOAP-fault parsing to actual Fault elements.
- Hardened RTSP subprocess error redaction.
- Disabled environment proxy inheritance for private camera HTTP requests.
- Limited last-good snapshot fallback to 90 seconds and reuse it when diagnostics temporarily own the camera I/O lock.
- Preserved protocol errors instead of clearing them merely because an ICMP heartbeat succeeds.
- Updated documentation and translations for the ICMP-only heartbeat introduced in v0.5.1.
- Added monotonic PTZ request sequencing so a delayed direction cannot execute after a newer STOP.
- Added emergency STOP on page blur/hide and before starting a deep diagnostic.
- Added light automatic revalidation after a camera comes back online, without restoring periodic deep polling.
- Fixed media selection so each dual-lens channel keeps its own main/substream fallback.
- Avoided sending arbitrary ONVIF-discovered RTSP paths to the fixed snapshot endpoint.
- Bounded camera-controlled ONVIF lists and escaped profile tokens before reinserting them into SOAP requests.
- Added API no-store/security headers and hardened Home Assistant Ingress-relative URL construction.
- Treats unavailable ICMP capability as an unknown heartbeat state instead of falsely marking the camera offline or falling back to TCP probing.


## 0.5.1

- Removed connect-only TCP health checks against camera services.
- Changed the periodic online heartbeat to ICMP ping so it does not consume an embedded HTTP server connection slot.
- Removed TCP port pre-probing from ONVIF discovery; the App now sends the actual SOAP request directly.
- Removed the extra RTSP TCP reachability probe after ffprobe; RTSP state is derived from the real stream probe.
- Service status is now derived from successful CGI, port 9898, ONVIF SOAP and ffprobe operations instead of opening throwaway sockets.
- This specifically avoids a JA-A12 failure mode where a TCP connection opened and closed without speaking the expected protocol can leave the local service unresponsive.


## 0.5.0

- Added a low-impact background heartbeat that only opens TCP/80 to determine whether the camera is online.
- Full CGI, port 9898, ONVIF and RTSP discovery now runs at startup and only when a user explicitly requests a diagnostic refresh.
- Paused browser status polling and media refresh while the Home Assistant App tab/page is hidden.
- Media snapshots refresh only while the page is visible and the media card is on screen.
- Added read-only ONVIF diagnostics for device information, system date/time, network interfaces, scopes and service list.
- Added ONVIF video-source and audio-source discovery.
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
