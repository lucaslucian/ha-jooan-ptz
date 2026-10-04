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
| 8899/TCP | HTTP/SOAP ONVIF | CONFIRMADO-STOCK | `/onvif/device_service` respondeu `GetCapabilities` com HTTP 200 no JA-A12 testado |
| 7788/UDP | protocolo proprietário | HIPÓTESE/observação anterior | descoberta local; formato ainda não incorporado ao App |

A descoberta atual usa requisições reais dos protocolos em 80, 554, 9898 e 8899. Desde a v0.5.1 não são abertos sockets TCP descartáveis apenas para testar portas, porque esse padrão mostrou capacidade de deixar os serviços locais da JA-A12 sem resposta. A porta 7788 permanece somente documentada até termos o pacote de descoberta completamente descrito.

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

### Candidatos GoAhead encontrados em firmware relacionado

**CORROBORADO-EXTERNO / LABORATÓRIO v0.16.**

Código público de uma interface GoAhead de câmera IP expõe um par leitura/escrita que coincide com um endpoint já observado em câmeras JOOAN:

```text
GET ou POST-query /goform/getVideoSettings
GET               /goform/updateVideoSettings
```

Na interface encontrada, `updateVideoSettings` recebe campos como:

```text
rotation = NORMAL | VFLIP | MIRROR | MIRROR-VFLIP
ir       = AUTO | ON | OFF
flicker  = 50HZ | 60HZ
brightness / contrast / saturation = 0..100
nbrightness / ncontrast / nsaturation = 0..100
resolution / resolution2 / codec / quality / quality2 / fps
```

A mesma base expõe motion detection como:

```text
GET ou POST-query /goform/getmotiondetectSettings?motionEnable=&sensitivity=
GET               /goform/updatemotiondetectSettings
motionEnable = YES | NO
sensitivity  = 0..5
zonemask     = campo presente em código relacionado, embora comentado na UI analisada
```

Também foi encontrado:

```text
POST /goform/NTP
time_zone = valores legados como EBS_-03, AST_-04, PST_-08 ...
```

### Resultado na JA-A12 de referência

Teste direto em 2026-10-04:

- `GET /goform/getVideoSettings` manteve o worker HTTP ocupado até timeout;
- `POST /goform/getVideoSettings?...fields...` com corpo `n/a` respondeu **HTTP 200**;
- `GET /goform/getmotiondetectSettings` respondeu **HTTP 404**;
- `POST-query /goform/getmotiondetectSettings` também respondeu **HTTP 404**.

Isso reforça que a câmera tem um servidor HTTP muito limitado: uma requisição candidata que trava pode deixar ICMP/ping funcionando enquanto HTTP/CGI parece indisponível. A v0.16.1 passa a preferir POST-query, não repete método após timeout e não tenta um segundo método depois de 404. Um 404 limpo também é memorizado até o reinício do App para evitar bater repetidamente no mesmo CGI ausente.

Esses writers **não são considerados confirmados na JA-A12** apenas pela semelhança. A v0.16.1 prefere o formato POST-query (`body=n/a`) usado pela página GoAhead original e só tenta GET quando o método é explicitamente rejeitado. O modo seguro permite um round-trip no-op que reaplica os valores recém-lidos e compara novamente o CGI e a porta 9898.

Na v0.16.2 existe também um modo manual **Forçar sem readback** para a última validação de hardware. Se o reader não devolver estado suficiente, o laboratório envia somente o campo/valor já allowlisted selecionado, sem inventar os demais parâmetros e sem prometer rollback automático. Um HTTP 4xx/5xx do writer é retornado como resultado e encerra a tentativa sem outro readback. Escritas candidatas continuam manuais e nunca aceitam caminho, nome de parâmetro ou valor arbitrário.

O candidato `/goform/NTP` é tratado separadamente: a fonte pública usa ASP interno para leitura e não oferece um CGI de leitura equivalente. O laboratório envia somente `time_zone` e nunca envia servidor NTP ou intervalo de sincronização.

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

Na interface normal esses itens permanecem **read-only**. O Laboratório pode executar writers candidatos específicos e sempre os trata como experimentais até haver validação por readback.

### Mapeamentos confirmados na JA-A12 de referência

Os testes controlados já fecharam os seguintes pares de valor:

| Propriedade | Valores observados | Interpretação |
|---|---|---|
| `autotrack` | `0 / 1` | rastreamento automático desligado / ligado |
| `flipmirror` | `0 / 3` | opção Flip Mirror do CAM720 desligada / ligada |
| `floodlight` | `0 / 1 / 2 / 3` | infravermelho normal / modo LED branco / luz inteligente por detecção / infravermelho desligado |
| `mdsensitivity`, `sub_mdsensitivity` | `1 / 2 / 3` | baixa / média / alta |
| `mdarea`, `sub_mdarea` | máscara de 25 bits | `33554431 = 0x1ffffff` representa as 25 zonas ativas |
| `timezone` | `GMT±HH:MM` | formato OEM observado diretamente |

Nos testes de zona, as duas máscaras sempre mudaram juntas. O teste isolado do canto superior esquerdo produziu `33554431 → 33554430`, limpando o bit 0; o canto superior direito isolado correspondeu ao bit 4. Somado aos testes anteriores dos cantos opostos, isso é consistente com uma grade 5×5 em ordem de linha, bits 0..24.

A estratégia proposta para um futuro switch de detecção continua sendo explícita: usar todas as zonas (`0x1ffffff`) para habilitar e nenhuma zona (`0`) para desabilitar apenas se o writer correspondente for validado. Um `motionEnable` nativo, se confirmado pelo CGI legado, será preferível.

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

Os testes manuais anteriores confirmaram `/live/ch00_0` e `/live/ch01_0` individualmente. No primeiro teste da v0.3.x, abrir quatro `ffprobe` simultâneos fez todos os candidatos falharem com `Invalid data found when processing input`, apesar de a porta 554 e as credenciais RTSP estarem válidas.

A v0.4.0 passou a testar os candidatos **sequencialmente**, priorizando os dois caminhos já confirmados, e também pode acrescentar paths retornados por ONVIF `GetStreamUri`.

O `ffprobe` em TCP registra somente metadados seguros:

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

### Validação no firmware stock

**CONFIRMADO-STOCK.**

No JA-A12 stock usado no desenvolvimento:

- `8899/TCP` está acessível;
- `POST /onvif/device_service` responde como serviço ONVIF;
- `GetCapabilities` respondeu HTTP 200;
- essa operação inicial não exigiu autenticação.

Isso confirma que o ONVIF não é apenas artefato do retrofit externo: existe uma superfície ONVIF real no firmware stock testado.

> O fato de `GetCapabilities` responder sem autenticação não prova que Media/PTZ de escrita também sejam anônimos. Cada operação deve ser testada separadamente.

### Descoberta read-only v0.4

A v0.4 amplia o probe para operações que não alteram estado:

```text
GetCapabilities
GetProfiles
GetStreamUri
GetStatus
GetPresets
```

Regras:

- o App nunca segue o hostname retornado em um `XAddr`;
- somente o **path** do serviço ONVIF é aproveitado;
- todas as conexões continuam presas ao IP privado configurado da câmera;
- URIs RTSP vindas de `GetStreamUri` são sanitizadas antes de chegar à UI;
- usuário/senha RTSP não são retornados;
- `GetStatus` e `GetPresets` são apenas leitura;
- nenhum preset é criado, alterado, removido ou chamado nesta fase.

### Evidência externa complementar

O projeto público `ADCDS/jooan-w3u-local-firmware` trabalha com uma revisão específica JOOAN W3-U / JA-A12 e usa:

```text
POST /onvif/Ptz
profile token: profile_0
ContinuousMove
Stop
```

Isso continua sendo útil para comparar nomes de serviços e comportamento PTZ, mas a implementação deste App prioriza o que for validado diretamente no firmware stock.

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

1. usar `get_deviceFeatures` para estado/capacidade do SD e para mapear os modos de gravação;
2. ONVIF Recording/Search é mantido apenas para inventário: a JA-A12 expõe `OnvifRecordingToken_1` com tracks Video/Audio/Metadata, mas `GetRecordingJobs` retorna vazio;
3. ONVIF Replay foi descartado nesta revisão porque `GetReplayUri` retorna `ActionNotSupported`;
4. a v0.12 adiciona baseline/diff somente leitura dos campos OEM de gravação para observar o que muda quando o CAM720 alterna entre 24/7, movimento e agendado;
5. não implementar acesso ao cartão nem setter OEM com base apenas em nomes de campos ou no layout interno do retrofit.

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

## `SetDiagMode` — uso experimental restrito

Pesquisas públicas sobre câmeras JOOAN/CAM720 mostram que algumas revisões possuem um modo de diagnóstico acionado através de `SingleHandlebyCommand`. Esse mecanismo pode abrir um callback de diagnóstico poderoso e por isso não pode ser tratado como um comando comum.

Política do projeto a partir da v0.7:

- continua proibido criar endpoint `singleCMD` genérico;
- o navegador nunca fornece `authserverip`, porta, código de autorização ou payload arbitrário;
- o laboratório sempre permite `SetDiagMode enable=0` para forçar o modo desligado;
- o teste ativo usa um callback sink dedicado na porta fixa 49000;
- `diag_callback_ip` precisa ser IP privado/ULA, da mesma família e mesma sub-rede local da câmera;
- o navegador não fornece callback host, porta, código de autorização ou payload;
- o backend gera código de autorização efêmero e limita a janela do teste;
- o sink só aceita a conexão quando o peer é exatamente o IP configurado da câmera;
- o sink fecha a conexão sem ler, enviar ou encaminhar payload de comando;
- `SetDiagMode enable=0` é enviado no bloco de finalização, mesmo após falha/timeout;
- nenhuma API de shell ou comando arbitrário é criada.

Assim podemos pesquisar o mecanismo sem transformar o App em uma interface de execução genérica.


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

| Recurso | Estado |
|---|---|
| validar câmera/credenciais | confirmado |
| identificação LAN / porta 9898 | confirmado |
| PTZ CGI | confirmado |
| ONVIF PTZ ContinuousMove/Stop | confirmado |
| ONVIF auxiliary IR | comando aceito; efeito físico deve ser validado por revisão |
| ONVIF Events PullPoint | confirmado para MotionAlarm + CellMotionDetector |
| presets ONVIF | leitura vazia; escrita não suportada na JA-A12 |
| Imaging ONVIF | GetOptions somente; leitura/escrita de settings não suportadas |
| Recording/Search ONVIF | inventário parcial de RecordingToken/tracks |
| Recording Jobs ONVIF | lista vazia na JA-A12 |
| Replay ONVIF | GetReplayUri não suportado |
| RTSP / snapshots / preview | confirmado no projeto |
| OEM recording mapper | baseline/diff v0.12+ |
| OEM toggle mapper | baseline/diff v0.13 |
| setter OEM tracking/detecção/luzes/push/privacy | ainda não confirmado |
| playback SD OEM | em pesquisa |
| talk-back | em pesquisa |
| UDP 7788 | em pesquisa |

## Próximos testes no hardware stock

1. mapear `record_type`, `recordechannel` e demais campos mudando um único modo de gravação por vez no CAM720;
2. usar o mapper OEM para movimento, pessoa/veículo, tracking, iluminação, alertas e privacy;
3. validar quais campos mudam ao ligar/desligar cada recurso;
4. procurar setters locais apenas depois de cada leitura estar semanticamente mapeada;
5. investigar o mecanismo OEM de playback do microSD;
6. manter ONVIF Events como caminho preferido para eventos de movimento no futuro Home Assistant;
7. descrever o pacote UDP 7788 antes de qualquer descoberta automática.

## Hardware stock — observações de 2026-10-02

Sem registrar identificadores únicos do dispositivo, o teste stock confirmou:

- modelo reportado: JA-A12;
- codec declarado: H264;
- modo de lentes: `double`;
- motion detection principal e secundária disponíveis;
- auto tracking, person tracking, person detection e vehicle detection reportados como capabilities;
- LED, floodlight e luz auxiliar reportados;
- gravação e cartão SD reportados;
- várias agendas de gravação, notificação, luz, alarme e privacy/PTZ-hide presentes;
- porta 80 acessível;
- porta 554 acessível;
- porta 9898 acessível;
- porta 8899 acessível;
- `/onvif/device_service` respondeu `GetCapabilities` com HTTP 200.

Essas observações são de capability/state discovery. Um campo existir não significa que a escrita correspondente já esteja validada.


## Política de polling / redução de carga

A partir da v0.5.0, a câmera não é consultada continuamente para informações que mudam pouco.

### Inicialização

Uma descoberta completa é permitida no startup para preencher:

- identificação CGI;
- estado/capabilities da porta 9898;
- configuração e probe RTSP;
- serviços e diagnóstico ONVIF;
- informações de dispositivo/rede/data e hora;
- profiles, video sources, StreamUri, PTZ nodes/configurations/status e presets.

### Operação normal

Depois do startup, o background usa apenas ICMP ping para determinar `online/offline`. Nenhuma porta de serviço da câmera é aberta apenas para heartbeat.

Não são repetidos periodicamente:

- `getPlatformID`;
- `getNetWorkState`;
- `getAPLanP2PSupport`;
- `get_deviceFeatures`;
- `RtspConf`;
- ONVIF;
- ffprobe;
- snapshots.

### Interface

O navegador usa a Page Visibility API. Quando a aba/página do App está oculta, polling de status e mídia é suspenso. O card de mídia também usa IntersectionObserver, portanto snapshots não são solicitados enquanto o card estiver fora da área visível.

Isso reduz carga no servidor HTTP/RTSP embarcado e evita tráfego sem utilidade.


## Estabilidade de transporte — v0.5.2

A auditoria posterior aos testes de hardware introduziu proteções adicionais para a stack embarcada:

- todo acesso CGI/9898/ONVIF/RTSP é serializado no backend para evitar concorrência entre diagnóstico, PTZ e snapshots;
- diagnóstico profundo manual é executado em background e somente uma instância pode ficar ativa;
- ONVIF interrompe a sequência após falha de transporte ou autenticação, evitando séries de timeouts redundantes;
- chamadas SOAP recebem um pequeno intervalo entre transações;
- RTSP testa primeiro somente o stream principal de cada canal e usa substream como fallback;
- caminhos adicionais descobertos via ONVIF só são sondados se ainda faltarem streams funcionais;
- RtspConf possui cache curto em memória para evitar uma consulta HTTP a cada snapshot;
- o último snapshot válido tem TTL limitado, impedindo que uma imagem antiga seja servida indefinidamente;
- erros de subprocessos RTSP recebem sanitização adicional para impedir vazamento de credenciais.


## Referências públicas

As referências abaixo servem para corroborar superfícies e arquitetura. Elas não transformam um endpoint em **CONFIRMADO-STOCK** até que a JA-A12 de referência o valide diretamente.

- `peak3d/jooan-updater` — uso stock de `GetJsonConf` em câmera JOOAN;
- `woofilian/sharedmemmory` — páginas GoAhead `camera.asp`, `Motion_detect.asp` e `adm/management.asp` que documentam os pares `get/updateVideoSettings`, `get/updatemotiondetectSettings` e o formulário `POST /goform/NTP`;
- `ADCDS/jooan-w3u-local-firmware` — engenharia reversa de revisão W3-U/JA-A12 e comportamento local do `jooanipc`;
- pesquisa pública de engenharia reversa do CAM720/JA-A12 — usada apenas para entender superfícies locais e `SetDiagMode`; o projeto não implementa execução de shell de diagnóstico.

Sempre prevalece a evidência coletada na unidade stock usada pelo projeto.
