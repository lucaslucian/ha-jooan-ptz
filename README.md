<p align="center">
  <img src="docs/images/jooan-local-control-logo.svg" alt="JOOAN Local Control" width="250">
</p>

<h1 align="center">JOOAN Local Control</h1>

<p align="center">
  Controle e monitoramento local de câmeras JOOAN/CAM720 diretamente pela LAN no Home Assistant.
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-0.18.3-blue">
  <img alt="Stage" src="https://img.shields.io/badge/stage-experimental-orange">
  <img alt="aarch64" src="https://img.shields.io/badge/aarch64-yes-success">
  <img alt="amd64" src="https://img.shields.io/badge/amd64-yes-success">
</p>

## Instalação

[![Adicionar repositório ao Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Flucaslucian%2Fha-jooan-ptz)

Se o botão não preencher o repositório automaticamente:

1. Abra **Configurações → Apps → Loja de Apps**.
2. Abra **⋮ → Repositórios**.
3. Adicione `https://github.com/lucaslucian/ha-jooan-ptz`.
4. Instale **JOOAN Local Control**.
5. Informe IP, usuário e senha local da câmera.
6. Inicie o App e abra o painel pelo Home Assistant.

## O que o App faz hoje

| Recurso | Estado |
|---|---|
| Validação local CGI | ✅ |
| PTZ cima/baixo/esquerda/direita/stop | ✅ |
| PTZ ONVIF com velocidade | ✅ após descoberta manual, com fallback CGI |
| Informações do dispositivo e rede | ✅ |
| Leitura de capabilities/estado OEM na porta 9898 | ✅ |
| Estado de SD, gravação, detecção, tracking e luzes | ✅ leitura |
| Detecção de lente dupla | ✅ |
| Credenciais RTSP locais | ✅ backend-only |
| Snapshots JPEG | ✅ sob demanda |
| Preview MJPEG | 🧪 sob demanda |
| ONVIF 8899 | ✅ descoberta mínima sob demanda do PTZ |
| Escrita de configurações da câmera | ❌ removida |

## Interface

A v0.18.2 usa **Câmeras & PTZ como área principal no topo**. Abaixo dela, as informações somente leitura ficam agrupadas em seções expansíveis para reduzir a poluição visual:

- visão geral e saúde;
- duas lentes com Snapshot / Ao vivo / Parar;
- PTZ com velocidade ONVIF e fallback CGI;
- informações gerais;
- detecção, tracking, iluminação e alertas;
- SD, gravação e agendas;
- diagnóstico técnico somente leitura;
- todas as configurações/estados lidos.

<p align="center">
  <img src="docs/images/dashboard-overview.svg" alt="Interface atual do JOOAN Local Control" width="900">
</p>

## Uso de rede

A JA-A12 de referência mostrou comportamento sensível a várias conexões de mídia. Por isso a v0.18 é mais conservadora:

- na inicialização são feitas apenas leituras CGI/OEM já comprovadas;
- o monitoramento de online/offline usa ICMP;
- ONVIF só é descoberto uma vez quando o usuário realmente movimenta o PTZ;
- não existe mais botão de `ffprobe`/teste RTSP no painel;
- snapshots só abrem RTSP quando solicitados;
- preview ao vivo só abre RTSP quando solicitado;
- nenhum feed é iniciado automaticamente ao abrir o painel;
- ao iniciar os dois feeds, o primeiro é negociado antes do segundo.

## PTZ

O controle CGI continua sendo o caminho comprovado.

Na primeira movimentação PTZ, o App executa uma descoberta ONVIF mínima. Se houver PTZ ONVIF utilizável, tenta `ContinuousMove` para permitir velocidade variável; se não houver ou ocorrer falha, usa CGI automaticamente como fallback.

## Segurança

O backend:

- aceita apenas IP local permitido;
- não oferece proxy genérico de URL ou `singleCMD`;
- mantém credenciais HTTP/RTSP fora do navegador;
- usa caminhos RTSP e comandos PTZ allowlisted;
- não expõe firmware update, factory reset ou configuração de Wi-Fi;
- não possui mais endpoints de laboratório/escrita experimental.

## Portas locais conhecidas

| Porta | Protocolo | Uso |
|---|---|---|
| 80/TCP | HTTP | CGI, autenticação, PTZ e informações |
| 554/TCP | RTSP | vídeo e áudio sob demanda |
| 9898/TCP | HTTP | capabilities e estado OEM |
| 8899/TCP | ONVIF | descoberta e PTZ |
| 7788/UDP | proprietário | observado em pesquisa |

## Documentação

- [Documentação do App](jooan_ptz/DOCS.md)
- [Protocolo local / engenharia reversa](docs/LOCAL_PROTOCOL.md)
- [Compatibilidade](docs/COMPATIBILITY.md)
- [Changelog](jooan_ptz/CHANGELOG.md)

## Hardware de referência

A principal unidade usada no desenvolvimento é uma **JOOAN JA-A12 / CAM720 dual-lens**. Firmwares e revisões diferentes podem apresentar comportamento distinto.

A documentação de protocolo preserva descobertas históricas da engenharia reversa, inclusive tentativas que não foram promovidas para o App.
