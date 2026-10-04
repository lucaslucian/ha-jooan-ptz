# Direção visual do painel

A v0.18 consolida o **JOOAN Local Control** em uma única página, sem abas e sem área experimental separada.

## Princípios

- visual integrado ao Home Assistant;
- cards simples e responsivos;
- telemetria primeiro;
- mídia somente sob demanda;
- nenhuma configuração de escrita exposta;
- informações avançadas abaixo das funções principais.

## Estrutura

```text
┌──────────────────────────────────────────────────────────────┐
│ JOOAN Local Control · modelo · online/offline               │
├──────────────────────────────────────────────────────────────┤
│ VISÃO GERAL                                                  │
│ [dispositivo] [rede] [SD] [mídia]                           │
│ [recursos]                    [serviços locais]              │
├──────────────────────────────────────────────────────────────┤
│ CÂMERAS & PTZ                                                │
│ [Lente 1] [Lente 2]        [PTZ]        [Informações]        │
├──────────────────────────────────────────────────────────────┤
│ DETECÇÃO / ALERTAS                                           │
├──────────────────────────────────────────────────────────────┤
│ GRAVAÇÃO / SD / AGENDAS                                      │
├──────────────────────────────────────────────────────────────┤
│ DIAGNÓSTICO MANUAL                                           │
├──────────────────────────────────────────────────────────────┤
│ TODAS AS CONFIGURAÇÕES LIDAS                                 │
└──────────────────────────────────────────────────────────────┘
```

## Mídia

Cada lente oferece:

- Snapshot;
- Ao vivo;
- Parar.

Nada é aberto automaticamente. Ao iniciar os dois feeds, o primeiro precisa concluir a negociação antes do segundo ser solicitado.

## PTZ

- pad direcional;
- STOP;
- seletor de velocidade ONVIF;
- badge mostrando ONVIF ou fallback CGI.

## Informações gerais

Mostrar apenas leituras úteis:

- modelo;
- firmware;
- ID/MAC/IP;
- canais;
- codec;
- timezone;
- RTSP;
- ONVIF;
- último contato.

## Estado lido

A área inferior agrupa os valores brutos/interpretados em:

- OEM state;
- propriedades OEM;
- device features;
- capabilities;
- plataforma;
- rede;
- RTSP;
- ONVIF.

## Regras de UX

1. Nunca mostrar senha, `userkey`, RTSP key ou URL autenticada.
2. Nenhum controle de escrita de configuração.
3. Recursos ausentes aparecem como **Não detectado**, não como falha geral.
4. Erro RTSP/ONVIF não deve, por si só, marcar a câmera inteira como offline.
5. Nenhuma mídia é aberta automaticamente.
6. Diagnóstico de mídia permanece explícito e manual.
