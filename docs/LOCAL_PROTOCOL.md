# Protocolo local utilizado

Este documento registra somente as superfícies locais utilizadas pela versão atual do **JOOAN Local Control**.

A referência principal é uma JOOAN JA-A12 / CAM720 dual-lens com firmware original.

## HTTP/CGI — porta 80

A autenticação CGI utiliza:

```text
userid=<usuario>
userkey=MD5(<senha CGI>)
```

Endpoints usados:

| Endpoint | Uso |
|---|---|
| `/goform/SingleHandlebyCommand` | PTZ CGI |
| `/goform/getPlatformID` | identificação |
| `/goform/getNetWorkState` | estado de rede |
| `/goform/getAPLanP2PSupport` | capabilities LAN |
| `/goform/getOtherSetttings?singleCMD=RtspConf` | credenciais RTSP locais |

### PTZ CGI

Comandos permitidos pelo backend:

```text
up
down
left
right
stop
```

O navegador nunca pode fornecer um `singleCMD` arbitrário.

## Estado OEM — porta 9898

Endpoint:

```text
GET /get?singleCMD=get_deviceFeatures
```

A resposta contém `deviceFeatures` e propriedades do dispositivo. O App aplica allowlist antes de expor propriedades na interface.

Entre os dados utilizados estão:

- modelo e firmware;
- modo de lente;
- codec;
- SD e gravação;
- detecção de movimento;
- pessoa e veículo;
- tracking;
- LED e iluminação;
- timezone;
- agendas conhecidas.

Essa superfície é usada somente para leitura.

## RTSP — porta 554

Paths utilizados:

```text
/live/ch00_0
/live/ch01_0
```

As credenciais são obtidas pelo CGI e a URL autenticada é construída apenas no backend.

O App abre o RTSP somente para extrair um frame JPEG com FFmpeg. Não existe proxy RTSP genérico.

## ONVIF — porta 8899

A descoberta usada pelo PTZ é mínima:

1. `GetCapabilities`;
2. `GetProfiles`;
3. identificação do serviço e profile token PTZ.

Quando disponível, o movimento usa:

```text
ContinuousMove
Stop
```

O hostname informado em `XAddr` não é seguido. O backend reaproveita somente o path e mantém a conexão presa ao IP local configurado.

Se ONVIF falhar, o controle volta ao CGI.

## Heartbeat

O estado online/offline usa ICMP para evitar abrir conexões periódicas nos serviços embarcados da câmera.

## Proteção de segredos

Nunca são enviados para a interface ou logs sem mascaramento:

- senha da câmera;
- `userkey`;
- senha/chave RTSP;
- URL RTSP autenticada;
- tokens ou credenciais encontrados em respostas do dispositivo.

O App aceita apenas IP local RFC1918, ULA ou link-local.
