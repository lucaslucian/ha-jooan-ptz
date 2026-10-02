# JOOAN Local Control for Home Assistant

App experimental para controlar câmeras JOOAN/CAM720 compatíveis diretamente pela rede local, sem depender da nuvem JOOAN para os recursos implementados.

## Instalação pelo Home Assistant

No Home Assistant:

1. Abra **Configurações > Apps > Loja de Apps**.
2. Abra o menu de repositórios.
3. Adicione:

```text
https://github.com/lucaslucian/ha-jooan-ptz
```

4. Instale **JOOAN Local Control**.
5. Configure o IP local da câmera, usuário e senha.
6. Inicie o App e abra a interface pelo menu lateral.

## Recursos atuais (v0.3.0)

- Comunicação somente com IP privado/local configurado.
- Autenticação local via `userid` + `MD5(password)`.
- Validação com `/goform/getPlatformID`.
- Estado de rede com `/goform/getNetWorkState`.
- PTZ local: cima, baixo, esquerda, direita e parar.
- PTZ por pressionar/segurar: envia movimento no pressionamento e `stop` ao soltar.
- Leitura de recursos do dispositivo na porta 9898.
- Detecção experimental de lente dupla por `deviceFeatures["10008"] == "double"`.
- Confirmação das credenciais RTSP por `RtspConf`.
- Identificação dos caminhos RTSP locais `/live/ch00_0` e, quando detectado, `/live/ch01_0`.
- Interface via Home Assistant Ingress.
- Watchdog de saúde pelo Supervisor.
- Senha e hash de autenticação não são gravados nos logs.

## Política local-only

O App exige que `camera_ip` seja um endereço IP literal privado ou link-local. Destinos públicos de Internet são recusados deliberadamente.

## Protocolos locais conhecidos

| Porta | Uso |
|---|---|
| 80/TCP | CGI `/goform/`, autenticação, PTZ e informações |
| 554/TCP | RTSP |
| 9898/TCP | Recursos/propriedades do dispositivo |

## Documentação técnica

O inventário de endpoints, portas, propriedades observadas, pesquisa ONVIF/RTSP e notas de segurança está em:

- [docs/LOCAL_PROTOCOL.md](docs/LOCAL_PROTOCOL.md)

O documento separa explicitamente descobertas confirmadas no firmware stock de evidências vindas de projetos externos/retrofit.

## Origem do conhecimento

A implementação consolida o que já existia em:

- `lucaslucian/ha-jooan-ptz`
- `lucaslucian/joan_camcontrol`

O protocolo não é uma API pública documentada pela JOOAN. Foi identificado por engenharia reversa e testes com câmera real, portanto pode variar entre modelos e firmwares.

## Próximas etapas

- Mostrar vídeo RTSP diretamente no painel do App.
- Confirmar o segundo canal em hardware de lente dupla.
- Investigar áudio bidirecional local.
- Investigar gravações/cartão SD pela LAN.
- Adicionar descoberta opcional na sub-rede sem depender de nuvem.
- Expor controles como entidades nativas do Home Assistant, mantendo o App como backend local.
