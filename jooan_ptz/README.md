# JOOAN Local Control

![JOOAN Local Control](logo.png)

Controle, mídia e diagnóstico **local** para câmeras JOOAN/CAM720 compatíveis no Home Assistant.

### Recursos atuais

- PTZ local com `stop` automático ao soltar;
- descoberta e validação sequencial de streams RTSP;
- snapshots e preview MJPEG local sem expor credenciais RTSP ao navegador;
- diagnóstico HTTP/CGI, RTSP, porta 9898 e ONVIF;
- leitura de dispositivo, SD, gravação, detecção, tracking, iluminação e agendas;
- suporte a dual-lens quando reportado pelo firmware;
- ONVIF Events/PullPoint com separação de estados iniciais, duplicatas e transições;
- laboratório OEM com baseline/diff, planos de escrita e readback;
- laboratório v0.16 para candidatos GoAhead CGI encontrados em firmware relacionado:
  - `getVideoSettings` / `updateVideoSettings`;
  - `getmotiondetectSettings` / `updatemotiondetectSettings`;
  - candidato de timezone em `/goform/NTP`;
- `SetDiagMode` limitado ao callback seguro e ao desligamento explícito.

### Segurança e escopo

O App recusa destinos públicos, serializa o I/O com a câmera e mantém credenciais sensíveis somente no backend. Não existe proxy genérico de URL, `singleCMD`, SOAP, DP/MQTT ou shell de diagnóstico.

Firmware, reset de fábrica e configuração de Wi-Fi permanecem deliberadamente fora de escopo.

> **Status:** experimental. A principal referência de testes é uma JOOAN JA-A12/CAM720 dual-lens stock. Outros modelos e revisões podem expor somente parte das superfícies documentadas.

Consulte **Documentação** e a aba **Laboratório** antes dos testes de escrita.
