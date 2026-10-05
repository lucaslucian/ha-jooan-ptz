# JOOAN Local Control

![JOOAN Local Control](logo.png)

Controle e monitoramento **local** para câmeras JOOAN/CAM720 compatíveis no Home Assistant.

### Recursos atuais

- PTZ CGI validado com `stop` ao soltar;
- PTZ ONVIF com velocidade após descoberta manual, com fallback CGI;
- duas lentes apresentadas lado a lado;
- snapshots RTSP sob demanda;
- atualização visual do PTZ por snapshots, limitada a aproximadamente 1 frame/s;
- nenhum stream contínuo é mantido pelo App;
- leitura de dispositivo, rede, SD, gravação, detecção, tracking, iluminação e agendas;
- capabilities/estado OEM pela porta 9898;
- descoberta ONVIF mínima, acionada somente pela primeira movimentação PTZ;
- painel único, sem abas.

### Comportamento conservador de rede

A JA-A12 de referência mostrou instabilidade quando várias sessões RTSP/ONVIF são abertas em sequência. A v0.18.5:

- não abre RTSP na inicialização;
- não possui preview MJPEG contínuo;
- usa ICMP para heartbeat;
- não expõe ações de diagnóstico; ONVIF é consultado somente pelo fluxo PTZ;
- usa apenas capturas pontuais; durante PTZ captura somente a lente móvel, no máximo uma vez por segundo.

### Segurança e escopo

O App mantém credenciais somente no backend e não oferece proxy genérico de URL ou `singleCMD`.

Escrita de configuração, firmware, reset, Wi-Fi, SetDiagMode e o antigo Laboratório não fazem parte da operação normal.

> **Status:** experimental. A principal referência é uma JOOAN JA-A12/CAM720 dual-lens stock.
