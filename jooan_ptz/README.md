# JOOAN Local Control

![JOOAN Local Control](logo.png)

Controle local de câmeras JOOAN/CAM720 compatíveis pelo Home Assistant.

## Recursos

- duas lentes lado a lado;
- identificação visual da lente PTZ;
- snapshots RTSP sob demanda;
- PTZ CGI com `stop`;
- PTZ ONVIF com velocidade e fallback automático para CGI;
- acompanhamento visual do PTZ por snapshots em até 1 FPS;
- leitura de modelo, firmware, rede, SD, gravação, detecção, tracking, iluminação e agendas;
- heartbeat por ICMP;
- credenciais mantidas somente no backend.

O App usa snapshots em vez de manter vídeo contínuo porque a câmera de referência pode ficar instável com várias conexões simultâneas, principalmente quando um NVR ou outro software já consome o RTSP. Cada captura abre o stream apenas pelo tempo necessário para obter um frame e fecha a sessão em seguida.

## Uso

Configure o IP local, usuário e senha da câmera, inicie o App e abra **JOOAN Local Control** pelo painel do Home Assistant.

O App abre RTSP somente quando uma imagem é solicitada. A descoberta ONVIF acontece apenas quando necessária para o PTZ.

## Compatibilidade

Desenvolvido e validado principalmente com **JOOAN JA-A12 / CAM720 dual-lens**. Outras revisões podem ter diferenças de firmware e serviços locais.

Veja a documentação completa no repositório do projeto.
