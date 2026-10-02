# JOOAN / CAM720 local protocol notes

Este documento registra o que foi observado ou corroborado sobre a comunicação local da família JOOAN/CAM720 usada pelo projeto.

O objetivo do projeto é **operar a câmera pela LAN sem depender da nuvem para as funções implementadas**. Informações de cloud capturadas durante a engenharia reversa são usadas somente como pista sobre capacidades do hardware; o App não deve depender delas.

> Importante: JOOAN reutiliza nomes comerciais e modelos em revisões de hardware diferentes. Uma descoberta válida para uma JA-A12 não deve ser aplicada automaticamente a todas as unidades.

## Níveis de confiança

- **CONFIRMADO-STOCK**: observado diretamente em captura de uma JA-A12 com firmware original.
- **CONFIRMADO-PROJETO**: validado pelo código atual deste projeto ou por teste RTSP já realizado.
- **CORROBORADO-EXTERNO**: encontrado em engenharia reversa pública de uma revisão JA-A12/W3-U compatível, mas ainda não validado na nossa unidade stock.
- **HIPÓTESE**: candidato que deve ser testado antes de virar recurso.

## Serviços locais

| Porta | Protocolo | Estado | Uso |
|---|---|---|---|
| 80/TCP | HTTP | CONFIRMADO-STOCK | CGI `/goform/`, identificação, PTZ e configurações |
| 554/TCP | RTSP | CONFIRMADO-PROJETO | vídeo e áudio locais |
| 9898/TCP | HTTP | CONFIRMADO-STOCK | `get_deviceFeatures` e estado/capacidades |
| 8899/TCP | HTTP/SOAP ONVIF | CORROBORADO-EXTERNO | serviço OEM ONVIF/PTZ em revisão JA-A12 |
| 7788/UDP | protocolo proprietário | HIPÓTESE/observação anterior | descoberta local; formato ainda não incorporado ao App |

A v0.3.0 faz probe não destrutivo de 80, 554, 9898 e 8899. A porta 7788 permanece somente documentada até termos o pacote de descoberta completamente descrito.

## HTTP local stock — porta 80

### `GET /goform/getAPLanP2PSupport`

**CONFIRMADO-STOCK.**

Foi observado sem autenticação. A resposta inclui informações como:

- `device_id`
- `resolution`
- `fullview`
- `persondet`
- `DetectType`
- `audioselect`
- `model`
- `isquery`
- `rtspAuth`

Na v0.3.0 esse endpoint é usado apenas como identificação/capability probe.

### `GET /goform/getPlatformID`

**CONFIRMADO-STOCK.**

Usado para validar as credenciais e identificar a plataforma local.

Autenticação observada:

```text
userid = usuário local
userkey = MD5(senha local)
```

O hash não deve ser enviado para o frontend nem registrado em log.

### `GET /goform/getNetWorkState`

**CONFIRMADO-STOCK.**

Usado para ler o estado de rede reportado pela câmera.

### `GET /goform/getOtherSetttings?singleCMD=RtspConf`

**CONFIRMADO-STOCK.**

Retorna a configuração de autenticação RTSP. Pode conter usuário e segredo RTSP.

Regras do projeto:

- consultar somente no backend;
- nunca retornar a credencial para o navegador;
- nunca registrar senha, `key` ou `userkey`;
- usar o IP configurado pelo usuário para construir o RTSP, não hosts retornados pela câmera.

### `GET /goform/getOtherSetttings?singleCMD=GetJsonConf`

**CONFIRMADO-STOCK, mas deliberadamente NÃO exposto.**

Foi observado que `GetJsonConf` pode consultar material interno sensível da configuração da câmera.

Ele não é necessário para as funções normais do App e **não deve virar um proxy genérico** na API.

### `GET /goform/SingleHandlebyCommand`

**CONFIRMADO-STOCK.**

Comandos PTZ atualmente permitidos pelo App:

```text
up
down
left
right
stop
```

O backend usa allowlist fixa. O frontend nunca controla livremente o valor de `singleCMD`.

## Porta 9898 — `get_deviceFeatures`

### Endpoint

```text
GET http://CAMERA_IP:9898/get?singleCMD=get_deviceFeatures
```

**CONFIRMADO-STOCK.**

Na captura analisada ele respondeu sem `userid/userkey`.

### Chaves `deviceFeatures` observadas

```text
10004 10005 10006 10007 10008 10010 10011 10015
10020 10021 10026 10034 10036 10037 10042 10043
10044 10046 10048 10049 10054 10057 10058 10069
10070 10074 10075 10076 10077 10096
```

Mapeamentos com evidência suficiente no momento:

| Chave | Valor observado | Interpretação |
|---|---|---|
| `10007` | `H264` | codec de vídeo |
| `10008` | `double` | unidade de duas lentes/canais |
| `10043` | `4x|16x` | velocidades de avanço de playback |

As demais chaves numéricas permanecem **não nomeadas** no código até termos mapeamento confiável.

### Propriedades stock observadas

A resposta local continha as seguintes propriedades não secretas:

```text
SupportFormatProg
alarm_light_mode
alarm_light_schedule
alarm_light_switch
alarmsoundselect
audiosensitive
autotrack
buzzer
definition
device_id
device_ip
device_mac
device_model
device_version
flipmirror
floodlight
from_type
lastformattime
led
light_schedule
md_enable
mdarea
mdsensitivity
msgpush_enable
msgpush_schedule
newflood_light_schedule
newmsg_push_schedule
newrecord_schedule
pdarea
person_detect
person_track_enable
powerfrequency
ptz_covre_status
ptz_hide_mode
ptz_hide_schedule
qualitymode
recloopnum
record_enable
record_schedule
record_type
recordechannel
rectype
resolution
sdcard_excepreason
sdcard_free
sdcard_status
sdcard_total
solution
sound_alarm_schedule
sub_md_enable
sub_mdarea
sub_mdsensitivity
timezone
vehicle_detect
video_standard_red
week
yellowlight
```

A v0.3.0 usa **allowlist dessas propriedades**. Campos desconhecidos de outros firmwares não são automaticamente enviados à UI, reduzindo o risco de expor segredos que apareçam em revisões futuras.

### Estados que já podem virar entidades/sensores

Sem escrever nada na câmera já podemos ler:

- cartão SD: estado, erro, total e livre;
- gravação: habilitada, tipo e canal;
- detecção de movimento principal e secundária;
- sensibilidade e máscara/área de movimento;
- auto tracking;
- person tracking;
- detecção de pessoa;
- detecção de veículo;
- área de detecção de pessoa;
- LED;
- floodlight;
- luz auxiliar/amarela;
- `video_standard_red`;
- flip/mirror;
- estado/modo de ocultação PTZ;
- push de mensagens;
- sensibilidade de áudio;
- buzzer;
- qualidade;
- frequência elétrica;
- várias agendas de gravação, luz, alarme e privacidade.

Nesta fase esses itens são **read-only**.

## RTSP local

**CONFIRMADO-PROJETO.**

Porta padrão:

```text
554/TCP
```

Caminhos conhecidos/sondados:

```text
/live/ch00_0
/live/ch00_1
/live/ch01_0
/live/ch01_1
```

`ch00_0` e `ch01_0` já apareceram em testes anteriores da JA-A12. Os caminhos `*_1` são candidatos adicionais e são sondados pela v0.3.0 em vez de serem assumidos como válidos.

A v0.3.0 usa `ffprobe` em TCP para descobrir quais caminhos realmente contêm streams e registra somente metadados seguros:

- codec;
- tipo de stream;
- resolução;
- sample rate;
- número de canais de áudio.

A URL RTSP completa, usuário e senha não aparecem no retorno.

### Snapshot

A v0.3.0 consegue gerar JPEG diretamente do RTSP usando FFmpeg.

O endpoint do App aceita somente nomes de stream em allowlist:

```text
ch00_0
ch00_1
ch01_0
ch01_1
```

Não existe parâmetro de URL arbitrária.

## ONVIF / porta 8899

### Evidência externa

O projeto público `ADCDS/jooan-w3u-local-firmware` trabalha com uma revisão específica JOOAN W3-U / JA-A12 e descreve o serviço OEM ONVIF/PTZ na porta 8899.

Na revisão estudada por esse projeto, o adaptador ONVIF utiliza:

```text
POST /onvif/Ptz
profile token: profile_0
ContinuousMove
Stop
```

O projeto externo usa esse serviço localmente dentro da própria câmera após retrofit. Portanto isso é **forte evidência da existência do serviço OEM**, mas não garante que toda JA-A12 exponha exatamente a mesma superfície pela LAN no firmware stock.

### Implementação atual

A v0.3.0 executa somente um probe não destrutivo:

- testa se 8899/TCP está aberta;
- tenta caminhos Device Service conhecidos;
- envia somente `GetCapabilities`;
- registra status HTTP e se autenticação parece obrigatória;
- não envia movimento ou alteração.

PTZ continua usando o CGI já confirmado enquanto não validamos ONVIF stock na unidade real.

## Engenharia reversa pública da JA-A12/W3-U

Referência:

```text
https://github.com/ADCDS/jooan-w3u-local-firmware
```

A revisão documentada externamente é:

- produto: W3-U;
- modelo OEM: JA-A12;
- placa: JA-6621 V1.0;
- SoC: Ingenic T23N/Pike;
- RAM: 64 MiB;
- SPI NOR: 8 MiB;
- sensores: `cv2005` + `cv2005s1`;
- rádio: SeekWave SKW6316 / SV6160LITE;
- userspace: MIPS32r2 little-endian, uClibc.

**Não assumimos que nossa unidade tem essa placa apenas porque também reporta JA-A12.**

O projeto externo retém `jooanipc` como proprietário do hardware de mídia e cria uma camada local que bloqueia cloud/P2P. Isso confirma que várias capacidades OEM podem ser acionadas localmente, mas algumas implementações dependem de código executando dentro da câmera e não podem ser copiadas diretamente para um App externo do Home Assistant.

## DP/MQTT OEM identificado externamente

A engenharia reversa pública encontrou comandos usados pelo `jooanipc`.

### Presets PTZ

| Comando | Função observada |
|---:|---|
| `66485` | salvar preset/home |
| `66486` | listar presets |
| `66489` | atualizar/renomear preset |
| `66490` | excluir preset |
| `66491` | ir para preset |

Esses comandos **não são usados pela v0.3.0**. Primeiro tentaremos a superfície ONVIF stock; DP/MQTT fica como segunda opção de pesquisa.

### Segurança/RTSP

O projeto externo também usa:

| Comando | Uso |
|---:|---|
| `66516` | habilitar configuração de segurança |
| `66517` | sincronizar senha de segurança/RTSP |

Esses comandos mexem em credenciais e não serão experimentados automaticamente.

## MicroSD e gravações

O retrofit externo revelou a organização do gravador OEM na revisão estudada:

```text
/mnt/sd_card/JOOAN_RECORD/YYYYMMDD/
```

Os clips são armazenados dentro das pastas diárias e o retrofit os lista lendo diretamente o filesystem da câmera.

Isso **não prova que o firmware stock ofereça esse filesystem pela LAN**. Nosso App não tem acesso a `/mnt/sd_card` da câmera.

Consequência para o projeto:

1. usar `get_deviceFeatures` para estado/capacidade do SD agora;
2. continuar procurando uma API stock de listagem/playback;
3. não implementar acesso ao cartão com base apenas no layout interno do retrofit.

## Capabilities vistas no ecossistema CAM720

Capturas do aplicativo/cloud indicam que esta família anuncia recursos como:

- automatic tracking;
- preset positions;
- PTZ calibration;
- timed reboot;
- volume e speaker volume;
- SD formatting;
- new playback;
- fast-forward `4x|16x`;
- phone UTC time sync;
- motion detection push;
- area detection/alarm;
- white light mode;
- user recorded alarm audio;
- event/full-day recording selection.

Esses campos são **pistas de capacidade**, não dependências do App local. A implementação só deve habilitar um controle quando existir um caminho LAN confirmado para leitura/escrita.

## Comando proibido: `SetDiagMode`

Pesquisas públicas recentes sobre câmeras JOOAN/CAM720 mostram que algumas revisões possuem um modo de diagnóstico acionado através de `SingleHandlebyCommand`.

Esse caminho não tem utilidade para o Home Assistant e amplia desnecessariamente a superfície de ataque.

Política do projeto:

- nunca criar endpoint `singleCMD` genérico;
- nunca permitir `SetDiagMode`;
- nunca encaminhar comandos arbitrários fornecidos pelo navegador;
- novos comandos entram individualmente na allowlist após validação.

## Proteção de segredos

Nunca expor em `/api/status`, UI ou logs:

- senha da câmera;
- MD5 da senha / `userkey`;
- senha/`key` RTSP;
- `AuthKey`;
- `device_pwd`;
- `security_password`;
- tokens da conta/cloud;
- URLs RTSP contendo credenciais.

O App também exige IP literal privado/link-local e rejeita destino público.

## Matriz atual do App

| Recurso | v0.3.0 |
|---|---|
| validar câmera/credenciais | sim |
| identificação LAN | sim |
| leitura 9898 | sim |
| estado SD/detecção/luz/gravação | sim, read-only |
| PTZ CGI | sim |
| RTSP credential check | sim |
| descobrir caminhos RTSP com ffprobe | sim |
| snapshot RTSP | sim |
| probe de portas | sim |
| probe ONVIF GetCapabilities | sim |
| ONVIF PTZ | ainda não |
| presets | ainda não |
| live video no navegador | ainda não |
| áudio de escuta no navegador | ainda não |
| playback SD | ainda não |
| alterar tracking/detecção/luz | ainda não |
| talk-back | ainda não |
| descoberta UDP 7788 | ainda não |

## Próximos testes no hardware stock

1. confirmar o resultado do probe 8899;
2. capturar resposta ONVIF e descobrir autenticação exigida;
3. confirmar quais dos quatro caminhos RTSP existem e quais carregam áudio;
4. comparar resolução/codec de cada stream;
5. investigar `GetProfiles`, `GetPresets` e `GetStatus` apenas com operações read-only;
6. capturar tráfego do CAM720 ao criar/usar preset;
7. capturar tráfego ao alternar tracking, LED/floodlight e detecção;
8. investigar listagem/playback do microSD sem modificar firmware;
9. descrever pacote UDP 7788 antes de habilitar descoberta automática.
