# JOOAN Local Control

Controle local de câmeras JOOAN/CAM720 compatíveis pela LAN.

## Configuração

- **camera_ip**: IP privado/local da câmera. Hostnames e IPs públicos são rejeitados.
- **camera_user**: usuário local da câmera. Normalmente `admin`.
- **camera_password**: senha local da câmera.
- **http_port**: porta CGI local. Padrão `80`.
- **features_port**: porta de propriedades do dispositivo. Padrão `9898`.
- **rtsp_port**: porta RTSP. Padrão `554`.
- **onvif_port**: porta candidata do serviço ONVIF OEM. Padrão `8899`.
- **validation_interval**: intervalo de validação em segundos.
- **debug**: registra diagnóstico sem expor credenciais.

## Validação

Ao iniciar, o App executa em background:

- `getPlatformID`;
- `getNetWorkState`;
- `getAPLanP2PSupport`;
- `get_deviceFeatures` na porta 9898;
- confirmação RTSP com `RtspConf`;
- probe das portas locais;
- probe dos caminhos RTSP;
- probe ONVIF somente leitura.

A autenticação CGI usa:

```text
userid = usuário configurado
userkey = MD5(senha)
```

A senha e o hash não são enviados à interface.

## PTZ

O endpoint confirmado é:

```text
/goform/SingleHandlebyCommand?singleCMD=up|down|left|right|stop
```

A interface envia o comando de direção ao pressionar e envia `stop` ao soltar.

O backend usa allowlist fixa; não há proxy genérico de `singleCMD`.

## RTSP

O App consulta `RtspConf` para obter as credenciais no backend e usa `ffprobe` para testar:

```text
/live/ch00_0
/live/ch00_1
/live/ch01_0
/live/ch01_1
```

A UI recebe somente metadados de stream. A URL RTSP autenticada não é retornada.

## Snapshot

O App usa FFmpeg para obter um frame JPEG do RTSP.

Somente os quatro caminhos RTSP conhecidos podem ser usados. Não existe parâmetro de URL arbitrária.

## ONVIF

A porta padrão de probe é 8899. O App tenta somente `GetCapabilities` em caminhos Device Service conhecidos.

Nesta versão ONVIF não envia PTZ nem altera configurações.

## Diagnóstico local v0.3.0

A interface mostra:

- disponibilidade dos serviços locais;
- capacidades reportadas pela porta 9898;
- estado de SD/gravação/detecção/tracking/luzes;
- resultado do probe ONVIF;
- streams RTSP confirmados;
- codec, resolução e áudio;
- snapshots dos streams confirmados.

## Campos sensíveis

O backend não envia para a interface:

- senha da câmera;
- `userkey`;
- chave/senha RTSP;
- `AuthKey`;
- `device_pwd`;
- `security_password`;
- tokens de cloud.

Campos desconhecidos retornados por novos firmwares também não são expostos automaticamente.

## Pesquisa técnica

Veja [`docs/LOCAL_PROTOCOL.md`](../docs/LOCAL_PROTOCOL.md) para o inventário completo de endpoints, propriedades e descobertas de engenharia reversa.
