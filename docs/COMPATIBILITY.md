# Compatibilidade

A família JOOAN/CAM720 possui revisões de hardware e firmware diferentes. O App evita assumir que todos os modelos expõem os mesmos recursos.

## JA-A12 usada no desenvolvimento

| Recurso | Estado |
|---|---|
| HTTP/CGI porta 80 | Validado |
| PTZ CGI up/down/left/right/stop | Validado |
| porta 9898 / `get_deviceFeatures` | Validado |
| dual-lens | Detectado |
| SD / gravação / detecção / tracking / luzes | Detectado em leitura |
| credenciais RTSP locais | Obtidas |
| `ch00_0`, `ch01_0`, `ch00_1`, `ch01_1` | Paths conhecidos; disponibilidade pode variar conforme estado da câmera |
| ONVIF 8899 | Validado |
| ONVIF GetCapabilities/GetProfiles | Usados na descoberta mínima |
| ONVIF ContinuousMove/Stop | Validado; usado com fallback CGI |
| preview MJPEG | Experimental |
| snapshots RTSP | Funcionam na unidade de referência |
| escrita de configuração | Não suportada pelo App |
| talk-back | Não implementado |

## Observação RTSP importante

Nesta JA-A12 já foi observado `ffprobe` retornar:

```text
Invalid data found when processing input
```

nos quatro paths conhecidos enquanto a porta 554 continuava acessível.

Por isso:

- o App não usa mais scan RTSP na inicialização;
- teste de substream é manual;
- falha RTSP não define sozinha o estado online/offline da câmera;
- mídia contínua só é aberta quando o usuário solicita.

## Outros modelos

Em outros modelos, registre:

- modelo comercial;
- `device_model`;
- firmware;
- portas locais;
- paths RTSP;
- resultado ONVIF;
- recursos da porta 9898.

Nunca publique senha, `userkey`, chave RTSP ou credenciais de cloud.
