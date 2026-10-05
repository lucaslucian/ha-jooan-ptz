# JOOAN Local Control — documentação

## Configuração

| Opção | Padrão | Descrição |
|---|---:|---|
| `camera_ip` | obrigatório | IP local literal da câmera |
| `camera_user` | `admin` | usuário local |
| `camera_password` | obrigatório | senha local HTTP/CGI |
| `http_port` | `80` | HTTP/CGI |
| `features_port` | `9898` | capabilities e estado OEM |
| `rtsp_port` | `554` | RTSP |
| `onvif_port` | `8899` | ONVIF |
| `validation_interval` | `30` | intervalo do heartbeat ICMP |
| `debug` | `false` | logs adicionais com segredos mascarados |

## Painel principal

Em telas maiores, a linha operacional apresenta:

```text
Lente 1 | Lente 2 | PTZ
```

A lente associada ao perfil PTZ ONVIF é destacada. As informações gerais da câmera ficam em uma seção recolhida logo abaixo.

Cada lente oferece captura manual de frame. O App não mantém uma sessão de vídeo contínua.

## PTZ

O controle aceita:

- cima;
- baixo;
- esquerda;
- direita;
- stop.

Na primeira movimentação, o backend faz uma descoberta ONVIF mínima. Se houver perfil PTZ válido, usa `ContinuousMove` com a velocidade selecionada. Em caso de falha, utiliza CGI automaticamente.

Enquanto o botão direcional está pressionado, a lente PTZ pode ser atualizada por snapshots com limite de aproximadamente 1 FPS. Ao parar, novas capturas são feitas para atualizar a posição final.

## Leitura de estado

O painel apresenta, quando disponíveis:

- modelo e firmware;
- estado de rede;
- canais e codec;
- armazenamento SD;
- gravação;
- detecção de movimento;
- detecção de pessoa e veículo;
- tracking;
- LED e iluminação auxiliar;
- agendas e estados OEM conhecidos;
- estado dos serviços locais.

Esses dados são somente leitura.

## Inicialização e heartbeat

Na inicialização:

1. o CGI PTZ `stop` valida autenticação;
2. `getPlatformID` lê identificação;
3. `getNetWorkState` lê o estado de rede;
4. `getAPLanP2PSupport` lê capabilities LAN;
5. `get_deviceFeatures` lê estado/capabilities OEM;
6. `RtspConf` obtém as credenciais RTSP sem abrir mídia.

Depois disso, o heartbeat usa ICMP. Se a câmera continuar online mas a sessão autenticada precisar ser recuperada, o App repete somente a validação leve.

## RTSP

Paths usados para snapshots:

```text
/live/ch00_0
/live/ch01_0
```

A URL RTSP autenticada existe somente no backend.

### Estratégia de mídia

O App não mantém vídeo contínuo por uma limitação observada na JA-A12 de referência. A câmera pode se tornar instável quando vários clientes mantêm conexões RTSP/HTTP/ONVIF ao mesmo tempo, especialmente quando um NVR ou outro software já está consumindo o stream.

O comportamento observado inclui serviços locais parando de responder temporariamente enquanto a câmera ainda responde ICMP. Por isso a estratégia escolhida é abrir o RTSP somente para capturar um frame e fechar imediatamente a sessão. Durante PTZ, os snapshots continuam deliberadamente limitados e sequenciais.

## Segurança

- apenas IPs locais são aceitos;
- não há destino HTTP/RTSP arbitrário;
- paths RTSP e comandos PTZ são allowlisted;
- credenciais não são retornadas pela API do painel;
- o App não modifica firmware, Wi-Fi, rede ou configurações internas da câmera.
