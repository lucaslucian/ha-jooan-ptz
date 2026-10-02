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
| `camera_password` | obrigatório | senha local |
| `http_port` | `80` | HTTP/CGI |
| `features_port` | `9898` | capabilities/estado |
| `rtsp_port` | `554` | mídia RTSP |
| `onvif_port` | `8899` | candidato ONVIF |
| `validation_interval` | `30` | intervalo de validação em segundos |
| `debug` | `false` | diagnóstico adicional nos logs |

Para o primeiro teste, `debug: true` pode ajudar. Desative depois de concluir o diagnóstico normal.

## O que aparece no painel

### Estado geral

O App valida `getPlatformID` e informa se a câmera está acessível e se as credenciais locais foram aceitas.

### PTZ

Movimentos confirmados:

- cima;
- baixo;
- esquerda;
- direita;
- stop.

Ao manter uma direção pressionada, o movimento é iniciado. Ao soltar o botão, o App envia `stop`.

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

Esses recursos permanecem **somente leitura** até termos um comando LAN de escrita validado.

### RTSP

O App obtém a configuração RTSP somente no backend e testa, por allowlist:

```text
/live/ch00_0
/live/ch00_1
/live/ch01_0
/live/ch01_1
```

O `ffprobe` identifica quais streams realmente existem e coleta metadados como codec, resolução e áudio.

As credenciais RTSP e a URL autenticada não são devolvidas ao navegador.

### Snapshots

O FFmpeg gera um JPEG diretamente do RTSP para cada stream confirmado. A interface permite atualizar os snapshots sem revelar a senha RTSP.

### ONVIF

O App testa a porta configurada e tenta somente uma operação read-only de `GetCapabilities`.

Nesta versão:

- ONVIF PTZ não é usado;
- presets não são alterados;
- nenhuma configuração ONVIF é escrita.

## Diagnóstico profundo

O botão **Executar diagnóstico profundo** verifica:

1. HTTP/CGI;
2. porta 9898;
3. RTSP;
4. ONVIF;
5. caminhos RTSP conhecidos;
6. codec/resolução/áudio;
7. capabilities e estado reportados pela câmera.

Esse diagnóstico também é executado em background na inicialização.

## Segurança

O App foi desenhado para reduzir a superfície de risco da engenharia reversa:

- aceita somente IPv4 RFC1918/link-local ou IPv6 ULA/link-local;
- não aceita hostname ou endereço público como destino;
- não existe endpoint para URL arbitrária;
- não existe proxy genérico de `singleCMD`;
- PTZ usa allowlist;
- caminhos RTSP usam allowlist;
- propriedades da porta 9898 usam allowlist;
- `SetDiagMode` é explicitamente recusado pelos testes;
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

### PTZ funciona, mas RTSP não

Confirme porta 554 e as credenciais retornadas pela configuração local da câmera. O diagnóstico informa quais caminhos foram encontrados.

### Porta 9898 indisponível

Alguns firmwares podem não expor esse serviço. PTZ/RTSP podem continuar funcionando independentemente.

### ONVIF não encontrado

A porta 8899 é baseada em evidências da família JA-A12 e não é garantida em toda revisão. ONVIF é opcional nesta fase.

## Limitações atuais

Ainda não estão implementados como recursos estáveis:

- live video contínuo dentro do painel;
- presets/Home;
- alteração de tracking/detecção;
- controle de IR/floodlight;
- playback do microSD;
- talk-back;
- descoberta UDP 7788.

## Pesquisa técnica

Para detalhes de protocolo e níveis de confiança das descobertas, consulte:

- [LOCAL_PROTOCOL.md](../docs/LOCAL_PROTOCOL.md)
- [COMPATIBILITY.md](../docs/COMPATIBILITY.md)
- [UI_DESIGN.md](../docs/UI_DESIGN.md)
