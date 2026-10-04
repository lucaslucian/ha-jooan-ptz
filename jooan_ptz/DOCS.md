# JOOAN Local Control

O **JOOAN Local Control** fornece monitoramento e controle local para câmeras JOOAN/CAM720 compatíveis, com foco em LAN e sem cloud para os recursos implementados.

## Configuração

| Opção | Padrão | Descrição |
|---|---:|---|
| `camera_ip` | obrigatório | IP local literal da câmera |
| `camera_user` | `admin` | usuário local |
| `camera_password` | obrigatório | senha local CGI |
| `http_port` | `80` | HTTP/CGI |
| `features_port` | `9898` | capabilities/estado OEM |
| `rtsp_port` | `554` | RTSP |
| `onvif_port` | `8899` | ONVIF |
| `validation_interval` | `30` | heartbeat ICMP em segundos |
| `debug` | `false` | logs adicionais |

## Painel

A v0.18 usa uma única página organizada em seções.

### Visão geral

Mostra:

- online/autenticação;
- modelo, firmware e canais;
- rede;
- armazenamento;
- recursos principais;
- estado dos serviços locais.

### Câmeras & PTZ

Cada lente possui:

- **Snapshot** — captura um único frame;
- **Ao vivo** — abre o bridge RTSP → MJPEG;
- **Parar** — encerra somente o feed daquela lente.

Também existe um botão para iniciar os dois feeds. Para reduzir carga na câmera, o App aguarda a negociação da primeira lente antes de abrir a segunda.

Nenhum vídeo ou snapshot é aberto automaticamente ao carregar a página.

### PTZ

O CGI local validado permanece como fallback confiável.

Após uma descoberta ONVIF manual, quando houver perfil PTZ utilizável, o App tenta ONVIF `ContinuousMove` com velocidade selecionável. Em caso de erro ou rejeição, volta automaticamente para o CGI.

### Detecção e alertas

São exibidos em modo leitura os valores reportados para:

- motion;
- pessoa;
- veículo;
- tracking;
- LED;
- floodlight;
- buzzer;
- push;
- áudio.

### Gravação e SD

São exibidos:

- capacidade/livre/status do SD;
- estado da gravação;
- tipo/canal reportado;
- agendas conhecidas.

### Diagnóstico

A seção de diagnóstico é **somente leitura**. Não existem mais botões para descoberta ONVIF, `ffprobe` ou outros testes.

Nos testes da JA-A12, múltiplas conexões RTSP/ONVIF em sequência coincidiram com períodos em que a câmera continuava respondendo ping, mas seus serviços ficavam instáveis. Por isso o App não executa scan RTSP automático e não expõe mais esse tipo de teste na interface.

A descoberta ONVIF mínima ocorre apenas na primeira movimentação PTZ, porque ela é necessária exclusivamente para tentar velocidade variável. Se falhar, o CGI PTZ é usado.

### Todas as configurações lidas

A última seção consolida somente dados efetivamente retornados pela câmera:

- estado OEM;
- propriedades OEM allowlisted;
- device features;
- capabilities interpretadas;
- plataforma;
- rede/LAN;
- RTSP;
- ONVIF.

Esses valores são somente leitura.

## Inicialização e heartbeat

Na inicialização o App executa somente a validação leve:

1. CGI PTZ `stop` para autenticação;
2. `getPlatformID`;
3. `getNetWorkState`;
4. `getAPLanP2PSupport`;
5. `get_deviceFeatures`;
6. leitura da configuração/credenciais RTSP, sem abrir o stream.

Depois disso o processo em background usa ICMP para acompanhar online/offline. Nenhum diagnóstico adicional é disparado pela interface.

Se a câmera voltar à rede mas a autenticação do App estiver marcada como inválida, uma validação CGI leve pode ser refeita com intervalo de recuperação.

## RTSP

Paths conhecidos:

```text
/live/ch00_0
/live/ch01_0
/live/ch00_1
/live/ch01_1
```

O App prefere `*_1` para preview de baixa resolução e cai para `*_0` se necessário.

A URL autenticada é construída somente no backend:

```text
rtsp://usuario:senha@IP:554/live/chXX_Y
```

A URL completa e a senha não são devolvidas ao navegador.

## Comportamento observado nesta JA-A12

Nesta unidade, `ffprobe` já retornou:

```text
Invalid data found when processing input
```

para os quatro paths RTSP em determinados estados, mesmo com credenciais RTSP obtidas e a porta 554 aberta.

Isso é tratado como falha de mídia, não como prova de que a câmera inteira está offline.

## Laboratório

O Laboratório foi removido na v0.18.

Também foram removidos do App:

- rotas `/api/lab/*`;
- setters candidatos de configuração;
- SetDiagMode;
- porta callback 49000;
- `diag_callback_ip`.

As descobertas históricas continuam registradas na documentação de protocolo para referência, mas não são mais expostas na operação normal.

## Segurança

O App:

- restringe o destino a rede local;
- não aceita URL arbitrária;
- não oferece `singleCMD` genérico;
- mantém segredos fora da API web;
- serializa operações pesadas da câmera;
- limita paths RTSP;
- limita comandos PTZ;
- não implementa firmware/reset/Wi-Fi.
