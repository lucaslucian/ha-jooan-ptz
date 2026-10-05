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

A v0.18.4 usa uma única página organizada em seções, com as imagens das câmeras priorizadas e PTZ/informações gerais agrupados na coluna lateral.

### Visão geral

Mostra:

- online/autenticação;
- modelo, firmware e canais;
- rede;
- armazenamento;
- recursos principais;
- estado dos serviços locais.

### Câmeras & PTZ

Cada lente possui **Capturar frame**, que faz uma captura RTSP única.

Não existe mais bridge RTSP → MJPEG nem preview contínuo dentro do App. O stream contínuo pode ser tratado por outra integração do Home Assistant, como MotionEye.

Durante uma movimentação PTZ:

1. o comando PTZ é enviado primeiro;
2. a lente PTZ é atualizada por snapshots com intervalo mínimo de 1 segundo;
3. as capturas são sequenciais e nunca se sobrepõem;
4. a captura de imagem não segura o lock de controle PTZ, então um FFmpeg lento não pode atrasar o comando `stop`;
5. ao soltar o comando, o App envia `stop` e tenta capturar mais um frame mostrando a posição final.

Se a câmera demorar mais de um segundo para gerar uma imagem, a taxa real fica abaixo de 1 FPS; o App nunca abre várias capturas em paralelo.

### PTZ

O CGI local validado permanece como fallback confiável.

Na primeira movimentação, o App executa uma descoberta ONVIF mínima. Quando houver perfil PTZ utilizável, tenta ONVIF `ContinuousMove` com velocidade selecionável; em caso de erro ou rejeição, volta automaticamente para o CGI.

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

O App usa os streams principais `ch00_0` e `ch01_0` para snapshots, porque foram os caminhos mais confiáveis para captura de frame na unidade de referência. Os paths `*_1` permanecem apenas como informação conhecida da câmera.

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
