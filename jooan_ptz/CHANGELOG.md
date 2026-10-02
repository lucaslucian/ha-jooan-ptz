# Changelog

## 0.2.0

- Renomeado para JOOAN Local Control.
- Atualizada a estrutura de build para Supervisor 2026.04+.
- Removida dependência do antigo `BUILD_FROM`.
- Limitadas arquiteturas suportadas a `amd64` e `aarch64`.
- Migrado servidor web para Gunicorn.
- Adicionado watchdog de saúde.
- Removida publicação direta da porta web; acesso somente via Ingress.
- Adicionado bloqueio de destinos públicos de Internet.
- Portadas informações de dispositivo da integração `joan_camcontrol`.
- Adicionada consulta da porta 9898.
- Adicionada detecção experimental de lente dupla.
- Adicionada confirmação de configuração RTSP local.
- Melhorado PTZ para enviar `stop` ao soltar o botão.
- Mantida proteção de senha e `userkey` nos logs.

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
