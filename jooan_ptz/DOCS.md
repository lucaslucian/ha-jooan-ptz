# JOOAN Local Control

O **JOOAN Local Control** fornece controle e diagnóstico local para câmeras JOOAN/CAM720 compatíveis, com foco em funcionamento pela LAN e sem dependência de cloud para os recursos implementados.

## Antes de começar

Você precisa de:

- Home Assistant OS ou instalação com suporte a Apps;
- arquitetura `aarch64` ou `amd64`;
- câmera e Home Assistant com conectividade IP entre si;
- IP local fixo/reservado para a câmera;
- usuário e senha local da câmera.

A referência principal de teste é a **JOOAN JA-A12 dual-lens**. Outros modelos/firmwares podem oferecer apenas parte das funções.

## Configuração rápida

Na aba **Configuração** do App:

| Opção | Padrão | Descrição |
|---|---:|---|
| `camera_ip` | obrigatório | IP local literal da câmera |
| `camera_user` | `admin` | usuário local |
| `camera_password` | obrigatório | senha local usada pelos endpoints HTTP/CGI `/goform/`; não confundir com a chave/senha RTSP retornada pela própria câmera |
| `http_port` | `80` | HTTP/CGI |
| `features_port` | `9898` | capabilities/estado |
| `rtsp_port` | `554` | mídia RTSP |
| `onvif_port` | `8899` | candidato ONVIF |
| `diag_callback_ip` | vazio | IP LAN do host Home Assistant no mesmo /24 da câmera, usado somente pelo sink SetDiagMode seguro |
| `validation_interval` | `30` | intervalo do heartbeat ICMP em segundos; não abre portas da câmera |
| `debug` | `false` | diagnóstico adicional nos logs |

Para o primeiro teste, `debug: true` pode ajudar. Desative depois de concluir o diagnóstico normal.

## O que aparece no painel

A interface v0.16 organiza os dados e testes em seis áreas:

- **Visão geral** — saúde, dispositivo, armazenamento, serviços e estados principais;
- **Câmeras & PTZ** — preview ao vivo, snapshots, seleção de lente e PTZ;
- **Detecção** — movimento, pessoa, veículo, tracking, luzes e alertas em modo leitura;
- **Gravação** — SD, gravação e resumo de agendas;
- **Diagnóstico** — serviços, ONVIF amigável e JSON bruto recolhido em detalhes expansíveis.
- **Laboratório** — testes manuais, allowlisted e experimentais de escrita/assinatura de eventos; firmware/reset/Wi-Fi ficam fora.


### Estado geral

O App valida as credenciais enviando o comando PTZ seguro `stop`. `getPlatformID` é consultado separadamente como informação opcional do dispositivo.

### PTZ

Movimentos confirmados:

- cima;
- baixo;
- esquerda;
- direita;
- stop.

Ao manter uma direção pressionada, o movimento é iniciado. Ao soltar o botão, o App envia `stop`. As requisições PTZ da interface levam uma sequência monotônica para impedir que uma direção atrasada seja executada depois de um `stop` mais novo. A interface também tenta `stop` ao perder foco, ser ocultada ou iniciar diagnóstico profundo.

### Informações e capabilities

O App consulta:

- `getNetWorkState`;
- `getAPLanP2PSupport`;
- `get_deviceFeatures` na porta 9898.

Quando disponíveis, são mostrados dados de:

- modelo, firmware e MAC;
- cartão SD total/livre/status;
- gravação;
- motion detection;
- sensibilidade/área;
- auto tracking;
- person/vehicle detection;
- LED/floodlight;
- flip/mirror;
- privacy/PTZ hide;
- schedules reportados pelo firmware.

Na interface normal esses recursos permanecem **somente leitura**. Escritas candidatas ficam isoladas na aba Laboratório, sempre manuais, allowlisted e acompanhadas de readback.

### Laboratório de escrita OEM

A v0.15 adiciona um laboratório de escrita **guardado**. Ele não inventa um setter. Em vez disso:

- lê novamente os valores atuais via `get_deviceFeatures` na porta 9898;
- valida apenas alvos já mapeados no hardware: iluminação, zonas e sensibilidade de movimento, auto tracking, Flip Mirror e timezone;
- produz um plano exato de `before → after`;
- consulta o `GetJsonConf` comprovado usando somente a consulta fixa `SystemInfo/ProductName`;
- mantém a execução bloqueada enquanto nenhum writer local de configuração tiver sido confirmado.

Os planos atuais conhecem estes valores observados:

- `floodlight`: 0 IR, 1 LED branco, 2 inteligente, 3 IR/visão noturna desligado;
- `mdarea/sub_mdarea`: máscara 25 bits, 33554431 = todas as zonas e 0 = nenhuma;
- `mdsensitivity/sub_mdsensitivity`: 1 baixa, 2 média, 3 alta;
- `autotrack`: 0/1;
- `flipmirror`: 0/3;
- `timezone`: formato `GMT±HH:MM`.

O botão genérico de escrita OEM continua bloqueado. A v0.16 adiciona, em uma seção separada, candidatos CGI específicos encontrados em código público relacionado; eles não transformam o laboratório em um proxy genérico. `SetDiagMode` não é usado como shell para contornar essa restrição.

### Candidatos GoAhead CGI

A v0.16 acrescenta uma trilha experimental baseada em endpoints encontrados em interfaces GoAhead relacionadas e corroborados pela presença de `/goform/getVideoSettings` em câmeras JOOAN.

Leituras allowlisted:

```text
/goform/getVideoSettings
/goform/getmotiondetectSettings
```

Writers candidatos correspondentes:

```text
GET  /goform/updateVideoSettings
GET  /goform/updatemotiondetectSettings
POST /goform/NTP
```

O fluxo recomendado é:

1. **Testar leituras encontradas**;
2. executar um **round-trip sem alteração**, que reaplica somente os valores recém-lidos;
3. verificar o readback do CGI e da porta 9898;
4. somente então testar um setter candidato específico.

Os alvos de escrita expostos são fixos: motion on/off, sensibilidade 1/2/3, rotation, IR, flicker e o conjunto fechado de timezones legado. Para Motion/Video, o campo-alvo precisa ter aparecido primeiro na própria leitura da câmera. Caminho CGI e nome de parâmetro nunca vêm livres do navegador.

O candidato `/goform/NTP` fica separado porque a fonte pública não oferece um CGI de leitura equivalente. O App envia somente `time_zone`; não envia servidor NTP nem intervalo de sincronização.

### RTSP

O App obtém a configuração RTSP somente no backend e testa primeiro os caminhos conhecidos:

```text
/live/ch00_0
/live/ch01_0
/live/ch00_1
/live/ch01_1
```

O probe é **sequencial e mínimo**. Para cada canal reportado, o App testa primeiro o stream principal (`*_0`) e só testa o substream (`*_1`) se o principal falhar. A JA-A12 testada usa um servidor RTSP embarcado limitado e abrir sessões desnecessárias pode fazer streams válidos falharem.

Na v0.4, caminhos adicionais reportados por ONVIF `GetStreamUri` também podem ser testados, sempre mantendo o host preso ao IP local configurado.

O `ffprobe` identifica quais streams realmente existem e coleta metadados como codec, resolução e áudio.

As credenciais RTSP e a URL autenticada não são devolvidas ao navegador.

### Snapshots

O FFmpeg gera um JPEG diretamente do RTSP para cada stream confirmado. A interface permite atualizar os snapshots sem revelar a senha RTSP.

### Preview ao vivo

A v0.6 adiciona preview ao vivo sob demanda no painel. O navegador não recebe a URL RTSP nem as credenciais: o backend abre um único stream RTSP e o FFmpeg o converte para MJPEG local de baixa taxa (até 640 px e 6 fps).

Seleção da lente PTZ:

- quando ONVIF informa o `profile_token` usado pelo PTZ, o App cruza esse token com o `GetStreamUri` do profile;
- na JA-A12 de referência, `profile_0` aponta para `/live/ch00_0`, portanto o preview PTZ usa o canal `ch00`;
- a segunda lente continua disponível nos snapshots, mas não abre um segundo preview contínuo.

Regras de proteção:

- no máximo **um preview contínuo** fica ativo por vez;
- trocar de lente encerra o preview anterior antes de abrir o próximo;
- o App prefere o substream `*_1` da lente PTZ quando validado/descoberto por ONVIF;
- antes de declarar o preview ativo, o backend exige que o FFmpeg realmente entregue um frame MJPEG;
- se o substream não produzir frames, ele é fechado e o stream principal PTZ é tentado automaticamente;
- se não houver substream conhecido, usa o stream principal confirmado e reduz resolução/FPS no bridge;
- PTZ continua permitido durante o preview;
- snapshots, diagnóstico profundo e validações pesadas ficam suspensos enquanto o preview contínuo está ativo;
- ao ocultar/sair da página, a interface solicita o encerramento do preview.

### ONVIF

No hardware JA-A12 stock testado, a porta `8899` e o caminho `/onvif/device_service` responderam `GetCapabilities` com HTTP 200.

A v0.4 amplia a descoberta somente leitura para:

- `GetCapabilities`;
- `GetProfiles`;
- `GetStreamUri`;
- `GetStatus` PTZ;
- `GetPresets`.

Nenhuma dessas operações de descoberta altera a câmera. Na operação normal, o PTZ continua usando o CGI já validado; a aba Laboratório também oferece ContinuousMove/Stop ONVIF de curta duração, já validado na unidade de referência. Presets continuam fora do fluxo normal porque o firmware não os expõe de forma utilizável.

## Laboratório experimental

A v0.16 mantém a operação normal separada dos testes manuais e remove da interface caminhos que já foram encerrados na JA-A12 validada.

Permanecem na aba **Laboratório**:

- ONVIF `ContinuousMove` + `Stop`, já validado no hardware;
- ONVIF `SendAuxiliaryCommand` apenas para `tt:Irlamp|On` / `tt:Irlamp|Off`;
- ONVIF Events com descoberta e PullPoint de 5/15/30 segundos;
- mapper OEM de gravação via porta 9898 com baseline/diff;
- mapper OEM de recursos liga/desliga via porta 9898 com grupos allowlisted;
- laboratório GoAhead CGI com probe de leitura, round-trip no-op e setters candidatos específicos;
- `SetDiagMode` restrito: OFF fixo e callback seguro de curta duração.

Foram removidos da interface do laboratório na JA-A12:

- presets ONVIF: `GetPresets` retorna vazio e `SetPreset` é `ActionNotSupported`;
- Imaging write/path: `GetOptions` funciona, mas `GetImagingSettings` e `SetImagingSettings` são `ActionNotSupported`;
- Recording Job/Replay ONVIF: há um RecordingToken fixo com Video/Audio/Metadata, porém `GetRecordingJobs` retorna vazio e `GetReplayUri` é `ActionNotSupported`.

### Mapper OEM de liga/desliga

O mapper é somente leitura. Ele relê `get_deviceFeatures` na porta 9898 e expõe grupos fixos:

- **motion**: `md_enable`, `sub_md_enable`, sensibilidades e áreas;
- **smart_detection**: `person_detect`, `vehicle_detect`, `pdarea`;
- **tracking**: `autotrack`, `person_track_enable`;
- **lighting**: `led`, `floodlight`, `yellowlight`, alarm light e agendas relacionadas;
- **alerts**: `msgpush_enable`, `audiosensitive`, `buzzer`, seleção de alarme e agendas;
- **privacy**: `ptz_hide_mode`, `ptz_covre_status`, agenda privacy e `flipmirror`.

Fluxo recomendado:

1. escolher um grupo;
2. capturar linha de base;
3. alterar **uma única opção** no CAM720;
4. comparar;
5. registrar quais propriedades mudaram;
6. só depois procurar/implementar um setter local específico para esse recurso.

Nenhum setter OEM genérico foi adicionado. Pesquisa pública até aqui não forneceu um mapeamento de escrita local confiável o suficiente para essas propriedades da JA-A12.

O laboratório **não implementa** atualização de firmware, reset de fábrica, configuração de Wi-Fi, proxy genérico de `singleCMD`, SOAP arbitrário ou DP/MQTT arbitrário.

### Eventos e futura integração Home Assistant

O PullPoint foi confirmado no hardware stock. A câmera entregou `tns1:VideoSource/MotionAlarm` com `State=true` e `tns1:RuleEngine/CellMotionDetector/Motion` com `IsMotion=true`. Nos testes de 30 segundos, porém, o firmware repetiu esses dois estados com `PropertyOperation=Initialized` a cada PullMessages. A v0.9 preserva os dados brutos, mas também deduplica snapshots idênticos e separa estado inicial de transições reais por tópico.

Isso confirma um caminho LAN viável para uma futura integração Home Assistant sem polling contínuo: o backend poderá manter uma assinatura e atualizar entidades/eventos de movimento diretamente. Pessoa e veículo ainda não foram confirmados como tópicos ONVIF distintos.

O campo OEM `msgpush_enable` e suas agendas continuam sendo observados separadamente. Eles podem ajudar a mapear o mecanismo de notificação do fabricante, mas não são necessários para o evento de movimento local já confirmado.

## Diagnóstico profundo

O botão **Executar diagnóstico profundo** verifica:

1. HTTP/CGI;
2. porta 9898;
3. RTSP;
4. ONVIF;
5. caminhos RTSP conhecidos;
6. codec/resolução/áudio;
7. capabilities e estado reportados pela câmera;
8. ONVIF Device Information;
9. data/hora e timezone ONVIF;
10. interfaces de rede ONVIF;
11. scopes e lista de serviços ONVIF;
12. video sources e audio sources;
13. profiles e StreamUri;
14. PTZ nodes/configurations/status;
15. presets existentes.

O diagnóstico completo é executado **uma vez na inicialização**. Depois disso, o processo em background usa somente ICMP ping para saber se a câmera continua online, sem abrir conexão em HTTP, RTSP, 9898 ou ONVIF. Se a câmera reiniciar ou voltar à rede e estiver online porém ainda não autenticada no App, uma validação CGI leve pode ser tentada com intervalo mínimo de 5 minutos; ONVIF/RTSP profundo não é repetido automaticamente.

A interface também economiza tráfego: quando a aba/página do App não está visível, o navegador pausa polling de status e snapshots. Os snapshots só são atualizados quando a página está visível e o card de mídia está em uso/na tela. O diagnóstico profundo manual roda em background, uma instância por vez, sem manter a requisição HTTP do painel aberta durante todo o probe.

## Segurança

O App foi desenhado para reduzir a superfície de risco da engenharia reversa:

- aceita somente IPv4 RFC1918/link-local ou IPv6 ULA/link-local;
- não aceita hostname ou endereço público como destino;
- não existe endpoint para URL arbitrária;
- não existe proxy genérico de `singleCMD`;
- PTZ usa allowlist;
- caminhos RTSP usam allowlist;
- propriedades da porta 9898 usam allowlist;
- `SetDiagMode` não possui proxy genérico: apenas OFF fixo e o callback restrito do laboratório são expostos;
- senha, `userkey`, RTSP key, `AuthKey`, `device_pwd` e `security_password` não são expostos pela API do painel.

## Primeiro teste recomendado

Depois de instalar e iniciar:

1. confirme **Câmera autenticada e acessível pela LAN**;
2. teste cada direção PTZ e o stop;
3. verifique **Dispositivo** e **Capacidades e estado local**;
4. execute **Diagnóstico profundo**;
5. confira as portas em **Serviços locais**;
6. confira o resultado em **ONVIF**;
7. confira quais caminhos RTSP aparecem disponíveis;
8. teste os snapshots.

Se houver falha, copie os logs sem incluir senhas ou chaves.

## Problemas comuns

### O App não inicia

Verifique se `camera_ip` e `camera_password` foram preenchidos. Eles são obrigatórios.

### IP rejeitado

O App foi propositalmente limitado a endereços locais. IP público e CGNAT não são aceitos como alvo.

### Credenciais rejeitadas

`camera_password` deve ser a senha **HTTP/CGI local** usada pelos endpoints `/goform/`. Alguns firmwares usam uma credencial RTSP separada; o App obtém essa credencial depois, através de `RtspConf`. Não coloque a chave RTSP no campo `camera_password`.

### PTZ funciona, mas RTSP não

Confirme porta 554 e as credenciais retornadas pela configuração local da câmera. O diagnóstico informa quais caminhos foram encontrados.

### Porta 9898 indisponível

Alguns firmwares podem não expor esse serviço. PTZ/RTSP podem continuar funcionando independentemente.

### ONVIF não encontrado

A porta 8899 foi validada na JA-A12 usada no desenvolvimento, mas JOOAN possui revisões diferentes. Em outros firmwares o serviço pode usar outra porta, caminho, autenticação ou simplesmente não existir.

## Limitações atuais

Ainda não estão implementados como recursos estáveis:

- live video de baixa latência em formato nativo H.264/WebRTC (o painel usa bridge MJPEG local);
- presets/Home no firmware stock validado;
- controles estáveis de tracking/detecção/iluminação — hoje ainda são mapeamento e laboratório experimental;
- playback real do microSD pela LAN;
- talk-back;
- descoberta/decodificação do UDP 7788.

## Pesquisa técnica

Para detalhes de protocolo e níveis de confiança das descobertas, consulte:

- [LOCAL_PROTOCOL.md](../docs/LOCAL_PROTOCOL.md)
- [COMPATIBILITY.md](../docs/COMPATIBILITY.md)
- [UI_DESIGN.md](../docs/UI_DESIGN.md)
