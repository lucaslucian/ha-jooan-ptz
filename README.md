<p align="center">
  <img src="jooan_ptz/logo.png" alt="JOOAN Local Control" width="250">
</p>

<h1 align="center">JOOAN Local Control</h1>

<p align="center">
  Controle, diagnóstico e mídia de câmeras JOOAN/CAM720 diretamente pela rede local no Home Assistant.
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-0.9.0-blue">
  <img alt="Stage" src="https://img.shields.io/badge/stage-experimental-orange">
  <img alt="aarch64" src="https://img.shields.io/badge/aarch64-yes-success">
  <img alt="amd64" src="https://img.shields.io/badge/amd64-yes-success">
</p>

<p align="center">
  <img src="docs/images/dashboard-concept.jpg" alt="Conceito visual do painel completo do JOOAN Local Control" width="900">
</p>

<p align="center"><em>Conceito visual aprovado para a evolução do painel completo. A imagem representa a direção de UI e não uma captura da implementação atual.</em></p>

## Instalação

[![Adicionar repositório ao Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Flucaslucian%2Fha-jooan-ptz)

Se o botão não preencher o repositório automaticamente:

1. Abra **Configurações → Apps → Loja de Apps**.
2. Abra **⋮ → Repositórios**.
3. Adicione:
   `https://github.com/lucaslucian/ha-jooan-ptz`
4. Instale **JOOAN Local Control**.
5. Informe IP, usuário e senha local da câmera.
6. Inicie o App e abra **JOOAN Local Control** pelo Home Assistant.

## O que o App faz hoje

| Recurso | Estado |
|---|---|
| Validação local da câmera | ✅ |
| PTZ cima/baixo/esquerda/direita/stop | ✅ |
| Informações do dispositivo | ✅ |
| Estado de rede | ✅ |
| Leitura de capacidades na porta 9898 | ✅ |
| Estado de SD, gravação, detecção, tracking e luzes | ✅ leitura |
| Detecção de lente dupla | ✅ |
| RTSP / credenciais locais | ✅ |
| Descoberta de streams/codec/áudio via ffprobe | ✅ probe sequencial mínimo v0.5.2 |
| Snapshots JPEG via RTSP | ✅ após stream confirmado |
| ONVIF 8899 `/onvif/device_service` | ✅ validado no JA-A12 stock |
| ONVIF GetProfiles/GetStreamUri/GetStatus/GetPresets | 🔬 read-only v0.4 |
| Laboratório ONVIF PTZ / IR / presets / Imaging | 🧪 ações manuais allowlisted v0.9 |
| ONVIF Events / PullPoint | ✅ movimento local confirmado; snapshots/transições separados v0.9 |
| SetDiagMode | 🧪 somente desligamento seguro no laboratório |
| Vídeo contínuo no navegador | 🧪 MJPEG local sob demanda v0.6 |
| Playback do microSD | 🔬 pesquisa |
| Talk-back | 🔬 pesquisa |

## Uso de rede e atividade

O App evita consultar a câmera sem necessidade:

- na inicialização executa uma descoberta completa para preencher informações, capabilities, ONVIF e RTSP;
- depois disso o processo em background usa somente ICMP ping para acompanhar online/offline, sem abrir portas da câmera;
- a interface para de consultar `/api/status` quando a aba do App fica oculta;
- snapshots só são atualizados quando a aba está visível **e** o card de mídia está na área visível da página;
- diagnóstico completo só roda novamente quando solicitado manualmente.

## Filosofia local-only

As funções implementadas usam somente a comunicação LAN da câmera. O backend:

- exige IP literal RFC1918, ULA ou link-local;
- recusa destinos de Internet pública;
- não oferece proxy genérico de URL ou `singleCMD`;
- mantém senha, `userkey`, chave RTSP e outros segredos fora da API web;
- usa allowlists para comandos, caminhos RTSP e propriedades exibidas.

A câmera pode ser isolada da Internet e continuar usando os recursos locais implementados pelo projeto.

## Dispositivo usado na engenharia reversa

A principal referência de hardware real do projeto é uma **JOOAN JA-A12 / CAM720**, dual-lens. JOOAN reutiliza nomes comerciais em revisões diferentes; por isso o projeto faz capability probing e evita assumir que todo modelo oferece os mesmos endpoints.

## Interface

A direção visual definida para o painel completo é um dashboard integrado ao Home Assistant, com:

- cabeçalho de saúde/conectividade;
- snapshots ou vídeo das lentes;
- PTZ em card próprio;
- informações do dispositivo;
- status RTSP/ONVIF;
- SD/gravação;
- detecção e tracking;
- iluminação;
- diagnóstico local;
- ações avançadas separadas das funções de leitura.

Na v0.9 o painel possui Visão geral, Câmeras & PTZ, Detecção, Gravação, Diagnóstico e Laboratório. A área experimental concentra as novas escritas e testes de eventos, mantendo a operação normal separada. A referência completa continua documentada em [docs/UI_DESIGN.md](docs/UI_DESIGN.md).

## Documentação

- [Documentação do App](jooan_ptz/DOCS.md)
- [Protocolo local / engenharia reversa](docs/LOCAL_PROTOCOL.md)
- [Compatibilidade](docs/COMPATIBILITY.md)
- [Direção visual do painel](docs/UI_DESIGN.md)
- [Changelog](jooan_ptz/CHANGELOG.md)

## Portas locais conhecidas

| Porta | Protocolo | Uso |
|---|---|---|
| 80/TCP | HTTP | CGI, autenticação, PTZ e informações |
| 554/TCP | RTSP | vídeo e áudio |
| 9898/TCP | HTTP | capabilities e estado do dispositivo |
| 8899/TCP | ONVIF | Device Service validado; media/PTZ read-only em descoberta |
| 7788/UDP | proprietário | descoberta em investigação |

## Pesquisa

O projeto consolida informações obtidas em:

- `lucaslucian/ha-jooan-ptz`;
- `lucaslucian/joan_camcontrol`;
- capturas reais de tráfego CAM720/JOOAN;
- pesquisa pública sobre a família JA-A12/W3-U.

O protocolo não é uma API pública oficial da JOOAN e pode mudar conforme hardware e firmware.
