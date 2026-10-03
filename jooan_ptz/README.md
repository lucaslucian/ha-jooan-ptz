# JOOAN Local Control

![JOOAN Local Control](logo.png)

Controle e diagnóstico **local** para câmeras JOOAN/CAM720 compatíveis no Home Assistant.

### Recursos atuais

- PTZ local com stop automático ao soltar;
- descoberta e validação de streams RTSP;
- snapshots das lentes via FFmpeg;
- diagnóstico HTTP, RTSP, porta 9898 e ONVIF;
- informações de dispositivo, SD, gravação, detecção, tracking e iluminação;
- suporte a câmera dual-lens quando reportado pelo firmware;
- interface protegida pelo Home Assistant Ingress;
- laboratório v0.7 para testes ONVIF PTZ, IR, presets, Imaging e Events sem expor comandos arbitrários.

### Local-first

O App recusa destinos públicos e mantém credenciais sensíveis apenas no backend. As funções implementadas não dependem da cloud JOOAN.

> **Status:** experimental. A referência principal de testes é a JOOAN JA-A12 dual-lens. Outros modelos e revisões podem ter diferenças de protocolo.

Consulte a aba **Documentação** antes do primeiro teste para configuração, diagnóstico e limitações atuais.
