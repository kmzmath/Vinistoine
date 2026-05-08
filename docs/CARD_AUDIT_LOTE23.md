# Auditoria de cartas — Lote 23

Base auditada: `Vinistone_updated_lote22_manual_bugfixes.zip`.

## Método

A auditoria comparou o `cards.json` contra os handlers reais da engine, buscando:
- campos usados no JSON mas ignorados pelos handlers;
- targets/conditions que não passavam pelo fluxo correto;
- cartas com alvo sequencial;
- efeitos de mão/deck que exigem markers de carta modificada;
- efeitos condicionais diretos fora de `CONDITIONAL_EFFECTS`.

## Bugs prováveis corrigidos

- `Viní Formoso`: `ADD_COPY_TO_DECK` ignorava `attack_bonus`, `health_bonus` e `target.position`.
- `Estrategista`: `RETURNED_CARD` não era resolvido por `SET_COST`; custo temporário não revertia no fim do turno.
- `Rica`: `BUFF_HEALTH` ignorava `amount_source: FRIENDLY_MINION_COUNT`.
- `Limpa-Limpa`: `DAMAGE_ALL_ENEMY_MINIONS` ignorava `amount_source: ENEMY_MINION_COUNT`.
- `Frifas`: `DRAW_CARD_FROM_DECK` ignorava `filter` e `preferred_id`.
- `Viní Flamenguista`: `DRAW_CARD_FROM_DECK` ignorava `filter` e `preferred_id`.
- `Vinassito`: `DRAW_MINION_FROM_DECK` ignorava `preferred`.
- `Drenar Almas`: `DAMAGE` ignorava `lifesteal`.
- `Vineba Flamejante`: `DAMAGE_ADJACENT_MINIONS` ignorava `amount_source: SELF_ATTACK`.
- `Fúria do Viní Geladinho`: `DAMAGE` com condição direta agora respeita `TARGET_IS_FROZEN`.
- `Apimentada`: `DRAW_CARD` com condição direta agora respeita `TARGET_HAS_TRIBE`.
- `NinjaGui10`: `DESTROY` com condição direta agora respeita `TARGET_ATTACK_LESS_THAN_SELF_ATTACK`.

## Observações

A cobertura estrutural continua útil, mas não garante semântica. Estes bugs surgiram porque a `action` existia, mas campos específicos do JSON não eram lidos pelo handler.
