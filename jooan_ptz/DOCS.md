# JOOAN Local Control

Controle local de câmeras JOOAN/CAM720 compatíveis pela LAN.

## Configuração

- **camera_ip**: IP privado/local da câmera. Hostnames e IPs públicos são rejeitados.
- **camera_user**: usuário local da câmera. Normalmente `admin`.
- **camera_password**: senha local da câmera.
- **http_port**: porta CGI local. Padrão `80`.
- **features_port**: porta de propriedades do dispositivo. Padrão `9898`.
- **rtsp_port**: porta RTSP. Padrão `554`.
- **validation_interval**: intervalo de validação em segundos.
- **debug**: registra diagnóstico ocultando `userkey`.

## Validação

Ao iniciar, o App consulta `getPlatformID`, `getNetWorkState`, recursos na porta 9898 e `RtspConf`.

A autenticação CGI usa:

```text
userid = usuário configurado
userkey = MD5(senha)
```

A senha e o hash não são exibidos nos logs.

## PTZ

O endpoint confirmado é:

```text
/goform/SingleHandlebyCommand?singleCMD=up|down|left|right|stop
```

A interface envia o comando de direção ao pressionar e envia `stop` ao soltar.

## RTSP

O App consulta `RtspConf` para confirmar credenciais RTSP, mas não devolve essas credenciais pela API web.

Caminhos conhecidos:

```text
/live/ch00_0
/live/ch01_0
```

O segundo caminho só é apresentado quando o dispositivo indica lente dupla.

## Segurança

A porta web não é publicada no host; a interface usa Home Assistant Ingress. O destino da câmera precisa ser um IP privado/link-local literal.
