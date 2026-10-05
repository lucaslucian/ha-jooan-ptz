# Compatibilidade

A família JOOAN/CAM720 possui revisões diferentes de hardware e firmware. O App só habilita comportamentos necessários à operação atual e evita assumir que todas as câmeras expõem os mesmos serviços.

## JA-A12 / CAM720 usada no desenvolvimento

| Recurso | Estado |
|---|---|
| HTTP/CGI porta 80 | validado |
| PTZ CGI up/down/left/right/stop | validado |
| porta 9898 / `get_deviceFeatures` | validado |
| duas lentes | detectado |
| estado de SD, gravação, detecção, tracking e luzes | validado em leitura |
| credenciais RTSP locais | obtidas |
| `/live/ch00_0` e `/live/ch01_0` | usados para snapshots |
| ONVIF porta 8899 | validado |
| ONVIF `GetCapabilities` / `GetProfiles` | usados na descoberta mínima |
| ONVIF `ContinuousMove` / `Stop` | validado para PTZ |
| fallback PTZ CGI | validado |

## Comportamento de mídia

A unidade de referência é sensível à abertura de várias sessões de mídia em sequência. Quando outro software ou NVR já mantém um stream aberto, conexões adicionais podem coincidir com travamentos temporários dos serviços HTTP/RTSP/ONVIF, mesmo sem perda de ping.

Por isso o App:

- não abre RTSP na inicialização;
- não mantém stream contínuo;
- abre o RTSP apenas o suficiente para obter um snapshot;
- usa snapshots individuais e sequenciais;
- não executa varredura de streams;
- atualiza automaticamente apenas a lente PTZ durante movimento;
- usa ICMP para heartbeat.

Uma falha RTSP ou CGI isolada não é usada para declarar a câmera inteira offline nem, depois de uma autenticação válida, para concluir imediatamente que as credenciais deixaram de funcionar.

## Outras revisões

Para outra câmera, os pontos mais importantes são:

- modelo e firmware;
- quantidade de canais;
- portas HTTP, RTSP, features e ONVIF;
- disponibilidade dos paths RTSP;
- suporte ao PTZ CGI;
- disponibilidade do perfil PTZ ONVIF.

Nunca publique senha, `userkey`, chave RTSP ou outras credenciais ao reportar compatibilidade.
