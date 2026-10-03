# Compatibilidade

A família JOOAN/CAM720 contém várias revisões de hardware e firmware com nomes comerciais semelhantes. Por isso, o projeto trabalha com descoberta de capacidades em vez de assumir suporte universal.

## Níveis

| Nível | Significado |
|---|---|
| **Validado** | recurso testado diretamente em hardware stock |
| **Detectado** | serviço/capability reportado localmente pelo dispositivo |
| **Corroborado** | há evidência pública forte para a mesma família, ainda sem teste no nosso hardware |
| **Experimental** | implementado como probe seguro, precisa de mais amostras |

## JA-A12 usada no desenvolvimento

| Recurso | Estado |
|---|---|
| HTTP/CGI porta 80 | Validado |
| PTZ up/down/left/right/stop | Validado |
| Lente PTZ / canal `ch00` | Inferido com forte evidência: ONVIF PTZ usa `profile_0`, cujo StreamUri é `/live/ch00_0` |
| RTSP 554 | Validado |
| `ch00_0` | Validado |
| `ch01_0` | Validado |
| porta 9898 / `get_deviceFeatures` | Validado |
| dual-lens | Detectado |
| SD / recording / motion / tracking / light state | Detectado |
| `ch00_1` | Detectado via ONVIF `GetStreamUri` (640×360, 15 fps, 256 kbps); validação RTSP contínua pendente |
| `ch01_1` | Validado via probe manual da v0.6 (640×360 H.264) |
| ONVIF 8899 | Validado: `/onvif/device_service` respondeu `GetCapabilities` com HTTP 200 |
| `GetProfiles` / `GetStreamUri` / `GetStatus` / `GetPresets` | Experimental read-only v0.4 |
| ONVIF ContinuousMove/Stop | Laboratório v0.7; precisa validação no hardware stock |
| ONVIF auxiliary IR | Laboratório v0.7; câmera anunciou Irlamp On/Off, escrita precisa validação |
| ONVIF presets | GetPresets funciona, lista vazia na JA-A12; SetPreset = ActionNotSupported |
| ONVIF Events PullPoint | Validado: MotionAlarm/CellMotionDetector entregues localmente |
| UDP 7788 | Observado, formato em pesquisa |
| playback microSD | ONVIF Replay não suportado; investigação OEM ativa v0.12 |
| talk-back | Em pesquisa |

## Outros modelos

O App pode funcionar parcialmente em outros JOOAN/CAM720 que compartilhem os mesmos endpoints. Se um modelo diferente funcionar, registre:

- modelo comercial;
- `device_model`;
- versão de firmware;
- quais portas responderam;
- caminhos RTSP encontrados;
- resultado do probe ONVIF;
- recursos da porta 9898.

Nunca publique senha, `userkey`, chave RTSP, `AuthKey` ou tokens de cloud.
