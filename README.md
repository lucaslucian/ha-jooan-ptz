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

## Recursos atuais — v0.3.0

- Comunicação somente com IP privado/local configurado.
- Autenticação local via `userid` + `MD5(password)`.
- Validação com `/goform/getPlatformID`.
- Estado de rede com `/goform/getNetWorkState`.
- Identificação/capability probe com `/goform/getAPLanP2PSupport`.
- PTZ local: cima, baixo, esquerda, direita e parar.
- PTZ por pressionar/segurar: envia movimento no pressionamento e `stop` ao soltar.
- Leitura de recursos do dispositivo na porta 9898.
- Estado read-only de SD, gravação, movimento, tracking e iluminação quando reportado pela câmera.
- Detecção de lente dupla por `deviceFeatures["10008"] == "double"`.
- Confirmação das credenciais RTSP por `RtspConf`, somente no backend.
- Probe das portas HTTP, 9898, RTSP e ONVIF.
- Probe ONVIF somente leitura na porta configurável 8899.
- Descoberta dos caminhos RTSP realmente disponíveis com `ffprobe`.
- Leitura de codec, resolução e áudio dos streams.
- Snapshots JPEG locais gerados diretamente do RTSP.
- Interface via Home Assistant Ingress.
- Watchdog de saúde pelo Supervisor.
- Filtragem por allowlist para não expor propriedades desconhecidas ou segredos da câmera.
- Senha, `userkey`, chave RTSP e outros segredos não são devolvidos pela API de diagnóstico.

## Política local-only

O App exige que `camera_ip` seja um endereço IP literal privado ou link-local. Destinos públicos de Internet são recusados deliberadamente.

O backend não oferece proxy genérico de URL, CGI ou `singleCMD`. Cada comando é permitido individualmente.

## Protocolos locais conhecidos

| Porta | Uso |
|---|---|
| 80/TCP | CGI `/goform/`, autenticação, PTZ e informações |
| 554/TCP | RTSP |
| 9898/TCP | recursos/propriedades do dispositivo |
| 8899/TCP | candidato ONVIF OEM; sondado de forma read-only |
| 7788/UDP | descoberta proprietária; ainda não implementada |

## Diagnóstico profundo

Na inicialização o App inicia um diagnóstico profundo em background. Ele também pode ser executado manualmente pela interface.

O diagnóstico:

- testa as portas locais;
- consulta os endpoints HTTP já conhecidos;
- sonda quatro caminhos RTSP candidatos;
- usa FFmpeg/ffprobe sem expor a URL autenticada;
- tenta `GetCapabilities` no ONVIF;
- habilita snapshots apenas para caminhos RTSP fixos em allowlist.

## Documentação técnica

O inventário de endpoints, portas, propriedades observadas, pesquisa ONVIF/RTSP, comandos OEM encontrados e regras de segurança está em:

- [docs/LOCAL_PROTOCOL.md](docs/LOCAL_PROTOCOL.md)

O documento separa explicitamente:

- o que foi observado no firmware stock;
- o que já foi confirmado pelo projeto;
- o que foi corroborado por engenharia reversa externa;
- o que ainda é hipótese.

## Origem do conhecimento

A implementação consolida o que já existia em:

- `lucaslucian/ha-jooan-ptz`
- `lucaslucian/joan_camcontrol`

Também usamos como referência técnica pública, sem incorporar firmware/binaries:

- `ADCDS/jooan-w3u-local-firmware`

O protocolo não é uma API pública documentada pela JOOAN. Ele foi identificado por engenharia reversa e testes com câmera real, portanto pode variar entre modelos e revisões.

## Próximas etapas

- Transformar os streams RTSP confirmados em vídeo contínuo no navegador.
- Validar ONVIF stock e investigar `GetProfiles`, `GetStatus` e `GetPresets` read-only.
- Implementar presets/Home somente após confirmação local.
- Investigar áudio de escuta e talk-back sem cloud.
- Investigar gravações/cartão SD pela LAN sem modificar firmware.
- Descrever e implementar com segurança a descoberta UDP 7788.
- Validar controles de tracking, detecção, LED/floodlight e IR antes de habilitar escrita.
- Expor controles e sensores como entidades nativas do Home Assistant, mantendo o App como backend local.
