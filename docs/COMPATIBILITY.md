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
| RTSP 554 | Validado |
| `ch00_0` | Validado |
| `ch01_0` | Validado |
| porta 9898 / `get_deviceFeatures` | Validado |
| dual-lens | Detectado |
| SD / recording / motion / tracking / light state | Detectado |
| substreams `*_1` | Experimental |
| ONVIF 8899 | Corroborado + probe experimental |
| presets/Home | Corroborado, não habilitado |
| UDP 7788 | Observado, formato em pesquisa |
| playback microSD | Em pesquisa |
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
