# Direção visual do painel

A v0.18.4 consolida o **JOOAN Local Control** em uma única página com **Câmeras & PTZ como área principal**, priorizando área útil para as duas imagens. PTZ e informações gerais ficam empilhados na coluna lateral; todo o restante permanece abaixo em painéis expansíveis somente leitura.

## Identidade visual

A identidade do projeto usa a mesma paleta da extensão:

| Uso | Cor |
|---|---|
| Fundo | `#0b0d11` |
| Cards | `#12151b` |
| Cards secundários | `#171b22` |
| Bordas | `#2b323d` |
| Texto | `#f4f7fb` |
| Texto secundário | `#9ba7b5` |
| Azul principal | `#4da3ff` |
| Azul ativo | `#2588ef` |
| Online | `#4fd08b` |

A identidade voltou ao logo original do projeto: **câmera dentro da casa azul**, usado no GitHub e no Home Assistant. O layout do painel continua moderno e compacto, mas sem trocar a marca histórica do projeto.

Assets principais:

- `docs/images/jooan-local-control-logo.svg` — wordmark do GitHub;
- `jooan_ptz/icon.png` — ícone 128×128 do Home Assistant;
- `jooan_ptz/logo.png` — logo 250×100 do App Store;
- `docs/images/dashboard-overview.svg` — representação atual da interface.

## Princípios

- Câmeras e PTZ primeiro;
- visual integrado ao Home Assistant;
- cards simples e responsivos;
- mídia somente sob demanda;
- nenhuma configuração de escrita exposta;
- dados avançados abaixo das funções principais;
- azul usado para ação/ênfase, sem excesso de cores.

## Estrutura

```text
┌──────────────────────────────────────────────────────────────┐
│ JA-A12 · Online · FW · Fuso · Auth · RTSP snapshots         │
├──────────────────────────────────────────────────────────────┤
│ CÂMERAS & PTZ                                                │
│ [      Lente 1      ] [      Lente 2      ] │ [PTZ]         │
│                                             │ [Informações]  │
├──────────────────────────────────────────────────────────────┤
│ INFORMAÇÕES                                                  │
│ ▼ Resumo e serviços                                          │
│ ▶ Detecção e alertas                                         │
│ ▶ Gravação e armazenamento                                   │
│ ▶ Diagnóstico técnico                                        │
│ ▶ Todas as configurações lidas                               │
└──────────────────────────────────────────────────────────────┘
```

## Mídia

Cada lente oferece captura manual de **Snapshot**.

O painel não possui mais vídeo MJPEG contínuo. Enquanto o usuário mantém um comando PTZ, a lente PTZ recebe novas imagens em cadência de no máximo **1 FPS**; ao soltar, uma captura final atualiza a posição resultante.

Isso mantém a UI útil para enquadramento sem duplicar o stream contínuo já servido por integrações especializadas.

## PTZ

- pad direcional;
- STOP;
- seletor de velocidade ONVIF;
- badge mostrando ONVIF ou fallback CGI;
- descoberta ONVIF mínima somente no primeiro movimento quando necessária.

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

## Informações secundárias

A área inferior usa `<details>` nativo para manter a página compacta. O primeiro grupo pode iniciar aberto; os demais ficam recolhidos.

Os grupos são:

1. resumo e serviços;
2. detecção e alertas;
3. gravação e armazenamento;
4. diagnóstico técnico;
5. todas as configurações lidas.

## Regras de UX

1. Nunca mostrar senha, `userkey`, RTSP key ou URL autenticada.
2. Nenhum controle de escrita de configuração.
3. Recursos ausentes aparecem como **Não detectado**, não como falha geral.
4. Erro RTSP/ONVIF não deve, por si só, marcar a câmera inteira como offline.
5. Nenhuma mídia é aberta automaticamente.
6. Diagnóstico técnico é somente leitura.
7. A identidade visual do GitHub e do App Store deve usar a mesma marca e paleta da interface.
