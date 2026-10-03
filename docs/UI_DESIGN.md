# Direção visual do painel

Este documento registra a referência visual escolhida para a evolução do painel completo do **JOOAN Local Control**.

![Conceito visual do painel completo](images/dashboard-concept.jpg)

> Esta imagem continua sendo a referência visual aprovada. A v0.6 já implementa a primeira versão do dashboard nessa direção, mantendo recursos ainda não confirmados como leitura/diagnóstico em vez de controles de escrita.

## Estilo

A interface deve parecer parte do ecossistema Home Assistant:

- layout limpo, responsivo e baseado em cards;
- azul/ciano como destaque principal;
- verde para estado saudável/online;
- vermelho somente para erro real;
- boa leitura em desktop e mobile;
- suporte futuro a tema claro/escuro;
- informações técnicas avançadas recolhidas por padrão;
- ações destrutivas ou de escrita claramente separadas da telemetria.

## Estrutura desejada

### Cabeçalho

- nome do App;
- modelo da câmera;
- estado online/offline;
- indicação **LAN only**;
- último check;
- botão para diagnóstico profundo.

### Área principal

Em telas grandes:

```text
┌─────────────────────────────────────────────────────────────┐
│ JOOAN Local Control       Online · LAN only · JA-A12       │
├───────────────────┬─────────────────────────────────────────┤
│ PTZ               │ Câmera / Streams                       │
│                   │ ┌──────────────┐ ┌──────────────┐       │
│       ▲           │ │ Lens / ch00 │ │ Lens / ch01 │       │
│    ◀ STOP ▶       │ └──────────────┘ └──────────────┘       │
│       ▼           │                                         │
├───────────────────┼───────────────────┬─────────────────────┤
│ Diagnóstico       │ ONVIF / RTSP      │ Dispositivo         │
├───────────────────┼───────────────────┼─────────────────────┤
│ SD / gravação     │ Detecção/tracking │ Luz / privacy       │
└───────────────────┴───────────────────┴─────────────────────┘
```

## Cards

### PTZ

- pad direcional grande;
- stop central;
- feedback visual enquanto pressionado;
- futuros presets abaixo, somente quando validados.

### Mídia

- duas lentes lado a lado quando disponíveis;
- badge de stream principal/substream;
- codec e resolução em informação secundária;
- refresh/snapshot;
- futuro vídeo contínuo sem mostrar URL RTSP autenticada.

### Diagnóstico

Mostrar estados resumidos:

- Camera API;
- RTSP;
- porta 9898;
- ONVIF;
- autenticação;
- última validação.

O JSON bruto deve ficar em uma seção avançada expansível.

### Dispositivo

- modelo;
- firmware;
- MAC;
- IP;
- timezone;
- dual-lens;
- capabilities relevantes.

### SD e gravação

- estado do cartão;
- capacidade total;
- espaço livre;
- gravação habilitada;
- tipo/canal;
- schedules somente como detalhe.

### Detecção

- motion;
- sensitivity;
- person;
- vehicle;
- auto tracking.

Inicialmente read-only. Toggles só devem aparecer após validação de escrita LAN.

### Iluminação e privacy

- LED;
- floodlight;
- yellow light;
- flip/mirror;
- PTZ hide/privacy.

## Regras de UX

1. Nunca mostrar senha, `userkey`, RTSP key ou URL autenticada.
2. Nunca mostrar um controle de escrita se o backend só confirmou leitura.
3. Recursos não suportados devem aparecer como **Não detectado**, e não como erro.
4. Erro ONVIF não deve marcar a câmera inteira como offline.
5. Diagnóstico avançado não deve dominar a tela principal.
6. O painel deve continuar útil mesmo com a Internet da câmera bloqueada.

## Implementado na v0.6

- frontend separado em template, CSS e JavaScript;
- abas de Visão geral, Câmeras & PTZ, Detecção, Gravação e Diagnóstico;
- cards amigáveis para estado, serviços e capabilities;
- JSON bruto recolhido em detalhes expansíveis;
- duas lentes apresentadas como canais separados;
- preview contínuo local sob demanda;
- seleção automática de substream quando validado/disponível;
- PTZ ao lado do preview;
- suporte a tema claro/escuro;
- layout responsivo.

## Próximos refinamentos

- validar o substream da segunda lente;
- medir CPU/latência do bridge MJPEG no Raspberry Pi;
- decidir se MJPEG permanece como preview padrão ou se evolui para HLS/WebRTC/go2rtc;
- adicionar controles de escrita apenas conforme os comandos locais forem validados;
- adicionar presets somente após validação explícita de criação/chamada/remoção.
