# JOOAN PTZ

Controle local de câmeras JOOAN compatíveis através da rede LAN.

## Configuração

Configure no Home Assistant:

- **Camera IP**: endereço IP da câmera na rede local.
- **Camera User**: usuário da câmera. O padrão é `admin`, mas pode ser alterado.
- **Camera Password**: senha atual da câmera.
- **Debug**: habilitado por padrão durante a fase de desenvolvimento.

A senha nunca é exibida nos logs. O add-on calcula localmente o `MD5` da senha para gerar o `userkey` exigido pela API CGI local.

## Validação

Ao iniciar, o add-on verifica as credenciais usando o endpoint somente leitura:

`/goform/getPlatformID`

Se a resposta for válida, as informações retornadas pela câmera são mostradas no painel. O estado da rede também é consultado através de `/goform/getNetWorkState`.

Os controles PTZ permanecem desativados enquanto a câmera não estiver configurada, acessível ou autenticada.

## PTZ disponível

- Cima
- Baixo
- Esquerda
- Direita
- Parar

## Debug

O debug está ativado por padrão nesta fase para facilitar a engenharia reversa. Os logs registram endpoint, código HTTP e corpo retornado pela câmera, mas não registram senha nem `userkey`.

As respostas podem ajudar a identificar informações adicionais do dispositivo e futuramente descobrir dados relacionados a vídeo/stream.

## Privacidade e rede

O controle PTZ usa HTTP diretamente entre o add-on e a câmera na LAN. Não é necessário MQTT ou serviço de nuvem para os comandos locais confirmados.

## Limitações atuais

O stream de vídeo, home/reset e outros comandos ainda estão sendo investigados. O add-on não assume que exista um equivalente local para comandos descobertos originalmente via MQTT.
