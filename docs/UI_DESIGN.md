# Direção visual do painel

Este documento registra a referência visual escolhida para a evolução do painel completo do **JOOAN Local Control**.

![Conceito visual do painel completo](images/dashboard-concept.jpg)

> Esta imagem é a referência visual aprovada para o projeto. Ela é um conceito de interface, não uma captura da implementação atual. O frontend deve evoluir nessa direção sem exibir recursos que ainda não foram confirmados pelo backend.

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

## Próxima implementação visual

A próxima refatoração do frontend deverá substituir o HTML monolítico atual por componentes/cards seguindo esta especificação, preservando todos os endpoints e regras de segurança existentes.
