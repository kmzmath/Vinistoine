# Cobertura do `cards.json`

Relatório gerado por `python scripts/card_coverage.py`.

## Resumo

- Cartas carregadas: **241**
- Actions distintas usadas no JSON: **157**
- Handlers registrados na engine: **158**
- Actions usadas pelo JSON sem handler: **0**
- Ocorrências de action cobertas: **299/299** (100.0%)
- Triggers distintas: **35**
- Triggers ainda não disparadas pela engine: **0**
- Target modes distintos: **51**
- Target modes desconhecidos: **0**
- Condition types distintos: **14**
- Conditions sem suporte explícito: **0**

## Interpretação

- `Actions sem handler` são a prioridade funcional: a carta pode ser jogada, mas o efeito é registrado como `unimplemented_action`.
- `Triggers não disparadas diretamente` indicam efeitos passivos ou eventos que ainda precisam de integração na engine.
- `Target modes desconhecidos` devem ser resolvidos antes de criar novas cartas com esses modos.
- `Conditions sem suporte explícito` fazem `CONDITIONAL_EFFECTS` retornar falso e registrar `unimplemented_condition`.
