"""
Resolver de efeitos. Cada 'action' é um handler registrado.
Adicione novos handlers conforme implementa as ações faltantes.
"""
from __future__ import annotations
from typing import Callable, Optional
from .state import GameState, Minion, PlayerState, CardInHand, gen_id, MAX_HAND_SIZE, MAX_BOARD_SIZE
from .cards import get_card
from . import targeting

# Registry de handlers
HANDLERS: dict[str, Callable] = {}
# Histórico de quem registrou cada ação. Permite auditar overrides feitos
# pelos arquivos de patch (effects_lote*.py) e descobrir handler-fantasma.
HANDLER_REGISTRATIONS: dict[str, list[str]] = {}
# Lista das ações que não temos handler ainda (pra registrar warnings)
UNIMPLEMENTED_ACTIONS: set[str] = set()


def handler(action_name: str):
    def deco(fn):
        prev = HANDLERS.get(action_name)
        HANDLERS[action_name] = fn
        owner = f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__name__', '?')}"
        history = HANDLER_REGISTRATIONS.setdefault(action_name, [])
        history.append(owner)
        if prev is not None:
            # Não quebra o jogo, só registra para a auditoria. O último @handler
            # ainda vence (comportamento histórico). Quem quiser ver duplicatas
            # pode inspecionar HANDLER_REGISTRATIONS.
            import logging
            logging.getLogger(__name__).debug(
                "handler %s sobrescrito (%s -> %s)",
                action_name, history[-2] if len(history) > 1 else "?", owner,
            )
        return fn
    return deco


def _mitigate_minion_damage(state: GameState, target: Minion, amount: int) -> int:
    """Aplica mitigadores defensivos antes de escudo divino/dano.

    RESISTANT reduz 1 de dano de qualquer fonte. Se o dano virar 0, o
    Escudo Divino NÃO é quebrado.
    """
    original = amount
    if amount > 0 and target.has_tag("RESISTANT"):
        amount = max(0, amount - 1)
        state.log_event({
            "type": "damage_reduced",
            "target_id": target.instance_id,
            "source_amount": original,
            "final_amount": amount,
            "reason": "RESISTANT",
        })
    return amount


def damage_character(state: GameState, target, amount: int, source_owner: int,
                     source_minion: Optional[Minion] = None,
                     is_spell: bool = False,
                     is_attack: bool = False) -> int:
    """Aplica dano a um lacaio ou herói. Retorna dano realmente aplicado."""
    if amount <= 0:
        return 0
    if source_minion and source_minion.has_tag("CANNOT_DEAL_DAMAGE_THIS_TURN"):
        state.log_event({"type": "damage_prevented", "source": source_minion.instance_id,
                         "reason": "CANNOT_DEAL_DAMAGE_THIS_TURN"})
        return 0

    if isinstance(target, Minion):
        if not hasattr(state, "last_damage_sources"):
            state.last_damage_sources = {}
        state.last_damage_sources[target.instance_id] = {
            "source_owner": source_owner,
            "source_minion_id": source_minion.instance_id if source_minion else None,
            "is_spell": is_spell,
            "is_attack": is_attack,
        }
        if target.immune:
            return 0
        if is_spell:
            if target.has_tag("SPELL_TARGET_IMMUNITY"):
                return 0
            if target.owner != source_owner and (
                target.has_tag("ENEMY_SPELL_TARGET_IMMUNITY")
                or target.has_tag("FRIENDLY_SPELL_TARGET_ONLY")
            ):
                return 0
        amount = _mitigate_minion_damage(state, target, amount)
        if amount <= 0:
            state.log_event({
                "type": "damage_prevented",
                "target_kind": "minion",
                "target_id": target.instance_id,
                "reason": "RESISTANT",
            })
            return 0
        if target.divine_shield:
            target.divine_shield = False
            state.log_event({"type": "divine_shield_break", "minion": target.instance_id})
            return 0

        # Edu Cachorrão: dano reduz ataque em vez de vida. Morre quando ataque chega a 0.
        try:
            from .effects_lote15 import has_reduce_attack_instead_of_health
            reduce_attack_instead = has_reduce_attack_instead_of_health(target)
        except Exception:
            reduce_attack_instead = False
        if reduce_attack_instead:
            old_attack = target.attack
            target.attack = max(0, target.attack - amount)
            if target.attack <= 0:
                target.health = 0
            state.log_event({
                "type": "damage_reduced_attack_instead_of_health",
                "target_id": target.instance_id,
                "amount": amount,
                "old_attack": old_attack,
                "new_attack": target.attack,
            })
            return amount

        target.health -= amount
        state.log_event({
            "type": "damage",
            "target_kind": "minion",
            "target_id": target.instance_id,
            "target_name": target.name,
            "target_card_id": target.card_id,
            "target_owner": target.owner,
            "source_name": source_minion.name if source_minion else None,
            "source_card_id": source_minion.card_id if source_minion else None,
            "source_owner": source_owner,
            "amount": amount,
        })

        # Triggers de dano do lote 19.
        try:
            from .effects_lote19 import (
                fire_damage_taken_trigger,
                fire_damage_dealt_by_self_trigger,
            )
            fire_damage_taken_trigger(state, target, amount, source_owner, source_minion, is_spell=is_spell)
            fire_damage_dealt_by_self_trigger(state, source_minion, target, amount)
        except Exception:
            pass

        # GAIN_ATTACK_ON_DAMAGE: Baiano - ganha atk pelo dano levado
        if target.has_tag("GAIN_ATTACK_ON_DAMAGE"):
            target.attack += amount
            state.log_event({"type": "gain_attack_on_damage",
                             "minion": target.instance_id, "amount": amount})

        # POISONOUS: se source é minion e causou dano > 0, mata
        if source_minion:
            kills_via_poison = False
            if source_minion.has_tag("POISONOUS"):
                kills_via_poison = True
            else:
                # POISONOUS_VS_<TRIBE>: só mata se alvo tem essa tribo
                for tag in source_minion.tags:
                    if tag.startswith("POISONOUS_VS_"):
                        tribe = tag[len("POISONOUS_VS_"):]
                        if target.has_tribe(tribe):
                            kills_via_poison = True
                            break
            if kills_via_poison and target.health > 0:
                target.health = 0
                state.log_event({"type": "poison_kill", "minion": target.instance_id})

        # LIFESTEAL no source
        if source_minion and source_minion.has_tag("LIFESTEAL"):
            heal_character(state, state.player_at(source_minion.owner), amount)

        # REFLECT: se alvo tem tag REFLECT e source é minion, reflete o dano
        if target.has_tag("REFLECT") and source_minion is not None:
            # Evita loop infinito: damage_character só reflete se source != target
            if source_minion is not target:
                # Aplica reflect SEM ser is_spell (é dano físico refletido)
                # Usa source_minion=None pra não disparar veneno do refletor
                _apply_reflect(state, source_minion, amount, target.owner)

        return amount

    if isinstance(target, PlayerState):
        if target.hero_immune:
            return 0
        if is_spell and target.player_id != source_owner and getattr(target, "hero_spell_target_immune", False):
            return 0
        # absorve armadura primeiro
        absorbed = min(target.hero_armor, amount)
        target.hero_armor -= absorbed
        remaining = amount - absorbed
        target.hero_health -= remaining
        state.log_event({
            "type": "damage",
            "target_kind": "hero",
            "target_id": target.player_id,
            "target_name": f"Herói de P{target.player_id}",
            "target_owner": target.player_id,
            "source_name": source_minion.name if source_minion else None,
            "source_card_id": source_minion.card_id if source_minion else None,
            "source_owner": source_owner,
            "amount": amount,
            "armor_absorbed": absorbed,
        })
        if remaining > 0:
            try:
                from .effects_lote19 import fire_self_hero_takes_damage_triggers
                fire_self_hero_takes_damage_triggers(state, target, remaining, source_owner, source_minion)
            except Exception:
                pass
        if source_minion and source_minion.has_tag("LIFESTEAL"):
            heal_character(state, state.player_at(source_minion.owner), amount)
        return amount

    return 0


def _apply_reflect(state: GameState, attacker: Minion, amount: int, reflector_owner: int):
    """Aplica dano refletido ao atacante. Não dispara reflect recursivo nem
    veneno (parâmetros simples)."""
    amount = _mitigate_minion_damage(state, attacker, amount)
    if amount <= 0:
        state.log_event({"type": "damage_prevented", "target_kind": "minion",
                         "target_id": attacker.instance_id, "reason": "RESISTANT"})
        return
    if attacker.divine_shield:
        attacker.divine_shield = False
        state.log_event({"type": "divine_shield_break", "minion": attacker.instance_id})
        return
    attacker.health -= amount
    state.log_event({
        "type": "damage",
        "target_kind": "minion",
        "target_id": attacker.instance_id,
        "target_name": attacker.name,
        "target_card_id": attacker.card_id,
        "target_owner": attacker.owner,
        "source_owner": reflector_owner,
        "amount": amount,
        "reflected": True,
    })


def heal_character(state: GameState, target, amount: int) -> int:
    if amount <= 0:
        return 0
    if isinstance(target, Minion):
        before = target.health
        target.health = min(target.max_health, target.health + amount)
        healed = target.health - before
        if healed > 0:
            state.log_event({
                "type": "heal", "target_kind": "minion",
                "target_id": target.instance_id, "target_name": target.name,
                "target_card_id": target.card_id, "target_owner": target.owner,
                "amount": healed,
            })
        return healed
    if isinstance(target, PlayerState):
        before = target.hero_health
        target.hero_health = min(target.hero_max_health, target.hero_health + amount)
        healed = target.hero_health - before
        if healed > 0:
            state.log_event({
                "type": "heal", "target_kind": "hero",
                "target_id": target.player_id, "target_name": f"Herói de P{target.player_id}",
                "target_owner": target.player_id, "amount": healed,
            })
        return healed
    return 0


def draw_card(state: GameState, player: PlayerState, n: int = 1):
    """Compra n cartas. Aplica fadiga se deck vazio.
    
    Suporta:
    - Deck markers: card_ids prefixados que viram cartas modificadas
    - Trigger ON_DRAW: dispara efeitos da própria carta quando ela sai do deck

    Também atualiza ``state.last_drawn_card_instance_ids`` com as cartas que
    foram efetivamente colocadas na mão nesta chamada. Isso permite efeitos
    encadeados como Foco, Guilãozinho e Investidor.
    """
    drawn_ids: list[str] = []
    for _ in range(n):
        if not player.deck:
            player.fatigue_counter += 1
            dmg = player.fatigue_counter
            damage_character(state, player, dmg, source_owner=player.player_id)
            state.log_event({"type": "fatigue", "player": player.player_id, "amount": dmg})
            continue
        raw = player.deck.pop(0)

        # Resolve marker de cards modificados (ex: Muriel adiciona "Hello World" custo 1)
        cost_override = None
        cost_modifier = 0
        stat_modifier = {}
        extra_tags = []
        card_id = raw
        deck_mods = getattr(state, "deck_card_modifiers", None)
        if deck_mods and raw in deck_mods:
            mod = deck_mods.pop(raw)
            card_id = mod["card_id"]
            cost_override = mod.get("cost_override")
            cost_modifier = int(mod.get("cost_modifier", 0) or 0)
            stat_modifier = dict(mod.get("stat_modifier") or {})
            extra_tags = list(mod.get("extra_tags") or [])

        if len(player.hand) >= MAX_HAND_SIZE:
            state.log_event({"type": "burn", "player": player.player_id, "card_id": card_id})
            continue

        new_card = CardInHand(
            instance_id=gen_id("h_"), card_id=card_id, cost_override=cost_override,
            cost_modifier=cost_modifier, stat_modifier=stat_modifier, extra_tags=extra_tags,
        )
        player.hand.append(new_card)
        drawn_ids.append(new_card.instance_id)
        state.log_event({
            "type": "draw", "player": player.player_id,
            "instance_id": new_card.instance_id, "card_id": card_id,
            "reason": getattr(state, "_draw_reason", None) or "effect",
        })

        if getattr(player, "reveal_next_draw", False):
            new_card.revealed = True
            state.log_event({
                "type": "reveal_drawn_card",
                "player": player.player_id,
                "instance_id": new_card.instance_id,
                "card_id": card_id,
            })

        # === ON_DRAW: dispara efeitos da carta que acabou de ser comprada ===
        card = get_card(card_id)
        if card:
            ctx = {"chosen_target": None, "is_spell": False,
                   "just_drawn_card": new_card,
                   "source_card_id": card_id}
            for eff in card.get("effects") or []:
                if eff.get("trigger") == "ON_DRAW":
                    resolve_effect(state, eff, player.player_id, None, ctx)

    state.last_drawn_card_instance_ids = drawn_ids

def summon_minion_from_card(state: GameState, owner: int, card_id: str,
                             position: Optional[int] = None,
                             stat_override: Optional[tuple[int, int]] = None) -> Optional[Minion]:
    """Cria um lacaio a partir de um card_id e o coloca em campo."""
    p = state.player_at(owner)
    if len(p.board) >= MAX_BOARD_SIZE:
        return None
    card = get_card(card_id)
    if card is None or card.get("type") != "MINION":
        return None
    atk = card.get("attack")
    if atk is None:
        atk = 0
    hp = card.get("health")
    if hp is None:
        hp = 1
    if stat_override:
        atk, hp = stat_override
    minion = Minion(
        instance_id=gen_id("m_"),
        card_id=card_id,
        name=card.get("name", card_id),
        attack=atk,
        health=hp,
        max_health=hp,
        tags=list(card.get("tags") or []),
        tribes=list(card.get("tribes") or []),
        effects=list(card.get("effects") or []),
        owner=owner,
        divine_shield="DIVINE_SHIELD" in (card.get("tags") or []),
        summoning_sick=True,
    )
    if position is None or position < 0 or position > len(p.board):
        position = len(p.board)
    if "DORMANT" in minion.tags:
        minion.cant_attack = True
        minion.immune = True
        minion.summoning_sick = True
        # Aceita qualquer effect com trigger FRIENDLY_MINIONS_SUMMONED, não só
        # AWAKEN. O próprio dispatch do trigger respeita conditions/targets.
        fms_threshold = next(
            (
                int(e.get("amount", 2) or 2)
                for e in (minion.effects or [])
                if e.get("trigger") == "FRIENDLY_MINIONS_SUMMONED"
            ),
            None,
        )
        if fms_threshold is not None:
            state.pending_modifiers.append({
                "kind": "friendly_summons_awaken_counter",
                "target_id": minion.instance_id,
                "owner": owner,
                "count": 0,
                "required": fms_threshold,
            })

    p.board.insert(position, minion)
    state.log_event({
        "type": "summon", "owner": owner, "minion": minion.to_dict()
    })

    # Dormant awaken counters: lacaios aliados dormentes acordam após N evocações.
    # A própria evocação do lacaio dormente não conta para ele.
    keep_modifiers = []
    for pm in state.pending_modifiers:
        if pm.get("kind") != "friendly_summons_awaken_counter":
            keep_modifiers.append(pm)
            continue
        if pm.get("owner") != owner:
            keep_modifiers.append(pm)
            continue
        target_id = pm.get("target_id")
        if target_id == minion.instance_id:
            keep_modifiers.append(pm)
            continue
        found = state.find_minion(target_id)
        if not found:
            continue
        target = found[0]
        if "DORMANT" not in target.tags:
            # Alvo não está mais dormente (acordou por outra rota): descarta o
            # contador, em vez de mantê-lo flutuando para sempre.
            continue
        pm["count"] = int(pm.get("count", 0) or 0) + 1
        required = int(pm.get("required", 2) or 2)
        if pm["count"] >= required:
            # Threshold batido: dispara FRIENDLY_MINIONS_SUMMONED como trigger
            # real para que o handler de AWAKEN (com conditions/target/etc do
            # JSON) decida o que fazer. Antes, o desperto era inline e ignorava
            # tudo isso.
            fire_minion_trigger(
                state, target, "FRIENDLY_MINIONS_SUMMONED",
                extra_ctx={
                    "summoned_minion": minion.instance_id,
                    "summoned_card_id": minion.card_id,
                    "fms_count": pm["count"],
                    "fms_required": required,
                },
            )
            state.log_event({"type": "friendly_minions_summoned_threshold",
                             "minion": target.instance_id, "count": pm["count"]})
            continue
        keep_modifiers.append(pm)
    state.pending_modifiers = keep_modifiers

    try:
        from .effects_lote19 import fire_friendly_summon_triggers
        fire_friendly_summon_triggers(state, minion, owner)
    except Exception:
        pass

    return minion


def grant_temporary_stat_buff(state: GameState, minion: Minion, attack: int = 0,
                              health: int = 0,
                              owner_for_revert: Optional[int] = None) -> None:
    """Aplica +ataque/+vida que se reverte no fim do turno do dono indicado.

    Diferente das tags _AURA_STAT (recalculadas a cada apply_continuous_effects),
    este buff é stat bruto: somamos no momento e registramos um pending_modifier
    `temporary_stat_buff` consumido em `engine._remove_temporary_tags_at_end_of_turn`.
    Use isto para "ganhe +X de ataque até o fim do turno" e similares.
    """
    atk = int(attack or 0)
    hp = int(health or 0)
    if atk == 0 and hp == 0:
        return
    if owner_for_revert is None:
        owner_for_revert = minion.owner
    if atk:
        minion.attack = max(0, minion.attack + atk)
    if hp > 0:
        minion.max_health += hp
        minion.health += hp
    elif hp < 0:
        minion.max_health = max(1, minion.max_health + hp)
        minion.health = min(minion.health, minion.max_health)
    state.pending_modifiers.append({
        "kind": "temporary_stat_buff",
        "owner": owner_for_revert,
        "minion_id": minion.instance_id,
        "attack": atk,
        "health": hp,
    })
    state.log_event({
        "type": "temporary_stat_buff",
        "minion": minion.instance_id,
        "attack": atk, "health": hp,
    })


# ======================= HANDLERS DE AÇÃO =======================

@handler("DAMAGE")
def _damage(state, eff, source_owner, source_minion, ctx):
    amount = eff.get("amount", 0)
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    is_spell = ctx.get("is_spell", False)
    for t in targets:
        damage_character(state, t, amount, source_owner, source_minion, is_spell=is_spell)


@handler("HEAL")
def _heal(state, eff, source_owner, source_minion, ctx):
    amount = eff.get("amount", 0)
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    for t in targets:
        heal_character(state, t, amount)


@handler("HEAL_FULL")
def _heal_full(state, eff, source_owner, source_minion, ctx):
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    for t in targets:
        if isinstance(t, Minion):
            heal_character(state, t, t.max_health)
        elif isinstance(t, PlayerState):
            heal_character(state, t, t.hero_max_health)


@handler("DRAW_CARD")
def _draw(state, eff, source_owner, source_minion, ctx):
    n = eff.get("amount", 1)
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    if not targets:
        targets = [state.player_at(source_owner)]
    for t in targets:
        if isinstance(t, PlayerState):
            old_reveal = getattr(t, "reveal_next_draw", False)
            if eff.get("reveal"):
                t.reveal_next_draw = True
            try:
                draw_card(state, t, n)
            finally:
                t.reveal_next_draw = old_reveal


@handler("BUFF_ATTACK")
def _buff_attack(state, eff, source_owner, source_minion, ctx):
    amount = eff.get("amount", 0)
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    for t in targets:
        if isinstance(t, Minion):
            t.attack += amount
            state.log_event({"type": "buff", "minion": t.instance_id,
                             "attack_delta": amount})


@handler("BUFF_HEALTH")
def _buff_health(state, eff, source_owner, source_minion, ctx):
    amount = eff.get("amount", 0)
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    for t in targets:
        if isinstance(t, Minion):
            t.health += amount
            t.max_health += amount
            state.log_event({"type": "buff", "minion": t.instance_id,
                             "health_delta": amount})


@handler("BUFF_STATS")
def _buff_stats(state, eff, source_owner, source_minion, ctx):
    # Suporta dois formatos:
    #   { action: BUFF_STATS, attack: 1, health: 1, ... }
    #   { action: BUFF_STATS, amount: {attack: 1, health: 1}, ... }
    amount = eff.get("amount")
    if isinstance(amount, dict):
        atk = amount.get("attack", 0)
        hp = amount.get("health", 0)
    else:
        atk = eff.get("attack", 0)
        hp = eff.get("health", 0)
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    for t in targets:
        if isinstance(t, Minion):
            t.attack += atk
            t.health += hp
            t.max_health += hp
            state.log_event({"type": "buff", "minion": t.instance_id,
                             "attack_delta": atk, "health_delta": hp})


@handler("DESTROY")
def _destroy(state, eff, source_owner, source_minion, ctx):
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    for t in targets:
        if isinstance(t, Minion):
            ctx["destroyed_minion_id"] = t.instance_id
            ctx["destroyed_minion_health"] = max(0, t.health)
            ctx["destroyed_minion_attack"] = max(0, t.attack)
            t.health = 0
            state.log_event({"type": "destroy", "minion": t.instance_id})


@handler("OPTIONAL_DESTROY_AND_GAIN_ATTRIBUTES")
def _optional_destroy_and_gain_attributes(state, eff, source_owner, source_minion, ctx):
    """Destrói um lacaio aliado escolhido e transfere atributos ao source.

    Usado por Lamboinha Má Cozinheiro. Se nenhum alvo foi escolhido, o efeito
    não faz nada. O alvo morre normalmente e seu Último Suspiro ainda será
    processado no cleanup.
    """
    if not source_minion:
        return
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    victim = next((t for t in targets if isinstance(t, Minion) and t is not source_minion), None)
    if victim is None:
        return

    gain = set(eff.get("gain") or [])
    if "ATTACK" in gain:
        source_minion.attack += max(0, victim.attack)
    if "HEALTH" in gain:
        source_minion.health += max(0, victim.health)
        source_minion.max_health += max(0, victim.health)
    if "TAGS" in gain:
        # Copia keywords funcionais, mas não copia triggers como Battlecry/Deathrattle
        # porque não copiamos os efeitos correspondentes.
        excluded = {"BATTLECRY", "DEATHRATTLE", "AURA"}
        for tag in victim.tags:
            if tag in excluded:
                continue
            if tag not in source_minion.tags:
                source_minion.tags.append(tag)
            if tag == "DIVINE_SHIELD":
                source_minion.divine_shield = True

    victim.health = 0
    state.log_event({
        "type": "destroy_and_gain_attributes",
        "source": source_minion.instance_id,
        "victim": victim.instance_id,
    })

    # Aplica efeitos adicionais descritos no JSON, como conceder RUSH.
    for extra in eff.get("additional_effects") or []:
        resolve_effect(state, extra, source_owner, source_minion, ctx)


@handler("REPLACE_HAND_WITH_RANDOM_CARDS_FROM_DECK")
def _replace_hand_with_random_cards_from_deck(state, eff, source_owner, source_minion, ctx):
    """Troca a mão atual por cartas aleatórias do próprio deck.

    As cartas da mão voltam ao deck, o deck é embaralhado e o jogador compra
    a mesma quantidade de cartas que tinha na mão.
    """
    p = state.player_at(source_owner)
    n = len(p.hand)
    returned = [c.card_id for c in p.hand]
    p.hand.clear()
    p.deck.extend(returned)
    state.rng.shuffle(p.deck)
    draw_card(state, p, min(n, MAX_HAND_SIZE))
    state.log_event({"type": "replace_hand_with_deck", "player": source_owner,
                     "returned": len(returned), "drawn": len(state.last_drawn_card_instance_ids)})


@handler("RETURN_TO_HAND_AND_MODIFY_COST")
def _return_to_hand_and_modify_cost(state, eff, source_owner, source_minion, ctx):
    target_desc = eff.get("target") or {}
    targets = targeting.resolve_targets(state, target_desc, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    cost_modifier = int(eff.get("cost_modifier", 0) or 0)
    cost_override = eff.get("cost")
    for t in list(targets):
        if not isinstance(t, Minion):
            continue
        owner = state.player_at(t.owner)
        if t in owner.board:
            owner.board.remove(t)
        if len(owner.hand) >= MAX_HAND_SIZE:
            state.log_event({"type": "return_to_hand_burned", "minion": t.instance_id})
            continue
        ch = CardInHand(instance_id=gen_id("h_"), card_id=t.card_id)
        if cost_override is not None:
            ch.cost_override = int(cost_override)
        ch.cost_modifier += cost_modifier
        owner.hand.append(ch)
        state.log_event({"type": "return_to_hand_modify_cost",
                         "minion": t.instance_id, "card": ch.instance_id,
                         "cost_modifier": cost_modifier})



def _return_minion_to_owners_hand(state: GameState, minion: Minion, *, cost_override=None,
                                  cost_modifier: int = 0) -> Optional[CardInHand]:
    """Remove um lacaio do campo e devolve a carta base à mão do dono."""
    owner = state.player_at(minion.owner)
    if minion in owner.board:
        owner.board.remove(minion)
    if len(owner.hand) >= MAX_HAND_SIZE:
        state.log_event({"type": "return_to_hand_burned", "minion": minion.instance_id})
        return None
    ch = CardInHand(instance_id=gen_id("h_"), card_id=minion.card_id)
    if cost_override is not None:
        ch.cost_override = int(cost_override)
    ch.cost_modifier += int(cost_modifier or 0)
    owner.hand.append(ch)
    state.log_event({"type": "return_to_hand", "owner": owner.player_id,
                     "minion": minion.instance_id, "card": ch.instance_id,
                     "card_id": ch.card_id})
    return ch


def _kill_minion_without_cleanup(state: GameState, minion: Minion, reason: str):
    """Marca um lacaio para morrer; cleanup resolverá deathrattles/cemitério."""
    minion.health = 0
    state.log_event({"type": reason, "minion": minion.instance_id,
                     "owner": minion.owner, "card_id": minion.card_id})


@handler("RETURN_ALL_MINIONS_TO_HAND")
def _return_all_minions_to_hand(state, eff, source_owner, source_minion, ctx):
    """Buraco Negro: devolve todos os lacaios à mão de seus donos.

    Isso não conta como morte, então não dispara Último Suspiro.
    """
    targets = targeting.resolve_targets(state, eff.get("target") or {"mode": "ALL_MINIONS"},
                                        source_owner, source_minion, ctx.get("chosen_target"))
    for t in list(targets):
        if isinstance(t, Minion):
            _return_minion_to_owners_hand(state, t)


@handler("SACRIFICE_FRIENDLY_MINION_DESTROY_ENEMY_MINION")
def _sacrifice_friendly_destroy_enemy(state, eff, source_owner, source_minion, ctx):
    """Gusnabo, o mago!: sacrifica um lacaio aliado para destruir um inimigo.

    O JSON usa `source` para o lacaio sacrificado e `target` para o inimigo.
    Quando jogado pelo cliente, `target_queue` deve ser [sacrifício, inimigo].
    """
    queue = ctx.get("target_queue") if isinstance(ctx.get("target_queue"), list) else []
    source_id = queue[0] if len(queue) >= 1 else ctx.get("source_target")
    target_id = queue[1] if len(queue) >= 2 else ctx.get("chosen_target")

    source_desc = eff.get("source") or {"mode": "CHOSEN", "valid": ["FRIENDLY_MINION"]}
    target_desc = eff.get("target") or {"mode": "CHOSEN", "valid": ["ENEMY_MINION"]}
    sacrifices = targeting.resolve_targets(state, source_desc, source_owner,
                                           source_minion, source_id)
    victims = targeting.resolve_targets(state, target_desc, source_owner,
                                        source_minion, target_id)
    sacrifice = next((t for t in sacrifices if isinstance(t, Minion)), None)
    victim = next((t for t in victims if isinstance(t, Minion)), None)
    if sacrifice is None or victim is None:
        return
    _kill_minion_without_cleanup(state, sacrifice, "sacrifice")
    _kill_minion_without_cleanup(state, victim, "destroy")


@handler("DEVOUR_FRIENDLY_MINION_GAIN_ATTRIBUTES")
def _devour_friendly_minion_gain_attributes(state, eff, source_owner, source_minion, ctx):
    """Spiid Faminto: consome um aliado e recebe atributos, bônus e texto."""
    if not source_minion:
        return
    target_desc = eff.get("target") or {"mode": "CHOSEN", "valid": ["FRIENDLY_MINION"]}
    chosen = ctx.get("chosen_target") or ctx.get("victim_id")
    targets = targeting.resolve_targets(state, target_desc, source_owner,
                                        source_minion, chosen)
    candidates = [m for m in state.player_at(source_owner).board if m is not source_minion]
    if getattr(state, "manual_choices", False) and chosen is None and candidates:
        state.pending_choice = {
            "choice_id": gen_id("choice_"),
            "kind": "choose_friendly_minion_to_devour",
            "owner": source_owner,
            "source_minion_id": source_minion.instance_id,
            "minions": [m.to_dict() for m in candidates],
        }
        state.log_event({"type": "choice_required",
                         "kind": "choose_friendly_minion_to_devour",
                         "player": source_owner})
        return
    victim = next((t for t in targets if isinstance(t, Minion) and t is not source_minion), None)
    if victim is None:
        victim = candidates[0] if candidates else None
    if victim is None:
        return

    source_minion.attack += max(0, victim.attack) + int(eff.get("bonus_attack", 0) or 0)
    source_minion.health += max(0, victim.health) + int(eff.get("bonus_health", 0) or 0)
    source_minion.max_health += max(0, victim.health) + int(eff.get("bonus_health", 0) or 0)
    if eff.get("copy_text"):
        existing = {(e.get("trigger"), e.get("action"), str(e.get("target"))) for e in source_minion.effects}
        for copied in victim.effects or []:
            key = (copied.get("trigger"), copied.get("action"), str(copied.get("target")))
            if key not in existing:
                source_minion.effects.append(dict(copied))
                existing.add(key)
        for tag in victim.tags:
            if tag not in source_minion.tags:
                source_minion.tags.append(tag)
            if tag == "DIVINE_SHIELD":
                source_minion.divine_shield = True
    _kill_minion_without_cleanup(state, victim, "devoured")
    state.log_event({"type": "devour_gain_attributes",
                     "source": source_minion.instance_id,
                     "victim": victim.instance_id})


@handler("DESTROY_AND_RESUMMON")
def _destroy_and_resummon(state, eff, source_owner, source_minion, ctx):
    """Renascimento: destrói um aliado e o renasce com modificações."""
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    target = next((t for t in targets if isinstance(t, Minion)), None)
    if target is None:
        return
    owner = target.owner
    board = state.player_at(owner).board
    try:
        pos = board.index(target)
    except ValueError:
        pos = len(board)
    base_card_id = target.card_id
    base_attack = target.attack
    target.health = 0
    if target in board:
        board.remove(target)
    state.graveyard.append({"card_id": target.card_id, "owner": owner, "name": target.name})
    state.log_event({"type": "destroy_no_deathrattle", "minion": target.instance_id,
                     "owner": owner})

    card = get_card(base_card_id) or {}
    base_hp = card.get("health")
    if base_hp is None:
        base_hp = max(1, target.max_health)
    resummon = eff.get("resummon") or {}
    atk = base_attack
    if resummon.get("attack_multiplier") is not None:
        atk = int(base_attack * float(resummon.get("attack_multiplier")))
    if resummon.get("attack") is not None:
        atk = int(resummon.get("attack"))
    hp = int(base_hp if resummon.get("health") == "FULL" else resummon.get("health", base_hp))
    new_m = summon_minion_from_card(state, owner, base_card_id, position=pos, stat_override=(atk, hp))
    if new_m:
        new_m.summoning_sick = True
        state.log_event({"type": "resummon", "old": target.instance_id,
                         "new": new_m.instance_id, "card_id": base_card_id})


@handler("DESTROY_AND_RESUMMON_FULL_HEALTH")
def _destroy_and_resummon_full_health(state, eff, source_owner, source_minion, ctx):
    """Igor Insano: destrói um aliado e o renasce com vida cheia."""
    clone = dict(eff)
    clone["action"] = "DESTROY_AND_RESUMMON"
    clone.setdefault("resummon", {"health": "FULL"})
    _destroy_and_resummon(state, clone, source_owner, source_minion, ctx)


@handler("REPLACE_FRIENDLY_MINIONS_FROM_DECK")
def _replace_friendly_minions_from_deck(state, eff, source_owner, source_minion, ctx):
    """Tirania: substitui seus lacaios por lacaios do deck até +N de custo.

    A escolha fina de qual carta usar ainda não tem UI própria; por enquanto o
    servidor escolhe deterministicamente o maior custo legal do deck para cada
    lacaio substituído. Como é substituição, não dispara deathrattle.
    """
    limit = int(eff.get("cost_increase_limit", 0) or 0)
    me = state.player_at(source_owner)
    targets = [t for t in targeting.resolve_targets(state, eff.get("target") or {"mode": "FRIENDLY_MINIONS"},
                                                    source_owner, source_minion, ctx.get("chosen_target"))
               if isinstance(t, Minion) and t.owner == source_owner]
    for old in list(targets):
        if old not in me.board:
            continue
        old_card = get_card(old.card_id) or {}
        old_cost = int(old_card.get("cost", 0) or 0)
        max_cost = old_cost + limit
        best_idx = None
        best_key = None
        for i, cid in enumerate(me.deck):
            card = get_card(cid)
            if not card or card.get("type") != "MINION":
                continue
            cost = int(card.get("cost", 0) or 0)
            if cost > max_cost:
                continue
            key = (cost, card.get("name", ""), -i)
            if best_key is None or key > best_key:
                best_key = key
                best_idx = i
        if best_idx is None:
            continue
        new_cid = me.deck.pop(best_idx)
        pos = me.board.index(old)
        me.board.remove(old)
        new_m = summon_minion_from_card(state, source_owner, new_cid, position=pos)
        if new_m:
            state.log_event({"type": "replace_minion_from_deck",
                             "old_card_id": old.card_id,
                             "new_card_id": new_cid,
                             "new_minion": new_m.instance_id})

@handler("ADD_TAG")
def _add_tag(state, eff, source_owner, source_minion, ctx):
    tag = eff.get("tag")
    if not tag:
        return
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    duration = eff.get("duration")
    # owner_for_revert determina QUEM termina o turno para a tag ser removida.
    #
    # - UNTIL_END_OF_TURN / THIS_TURN: revert no fim do turno do CASTER (mesmo
    #   turno em que a tag foi aplicada). Útil para "+X de ataque até o fim
    #   deste turno".
    # - UNTIL_OPPONENT_TURN_END / UNTIL_NEXT_TURN_END: revert no fim do turno
    #   do OPONENTE seguinte. Útil para "Furtividade por 1 turno" - a Furtividade
    #   precisa sobreviver ao turno inteiro do oponente para ter efeito
    #   defensivo.
    if duration in ("UNTIL_END_OF_TURN", "THIS_TURN",
                    "UNTIL_OPPONENT_TURN_END", "UNTIL_NEXT_TURN_END"):
        if duration in ("UNTIL_OPPONENT_TURN_END", "UNTIL_NEXT_TURN_END"):
            owner_for_revert = 1 - source_owner
        else:
            owner_for_revert = source_owner
        for t in targets:
            if isinstance(t, Minion):
                added_now = False
                if tag not in t.tags:
                    t.tags.append(tag)
                    added_now = True
                if tag == "DIVINE_SHIELD":
                    t.divine_shield = True
                state.pending_modifiers.append({
                    "kind": "temporary_tag",
                    "owner": owner_for_revert,
                    "minion_id": t.instance_id,
                    "tag": tag,
                    "remove_at": "end_of_turn",
                    "added_now": added_now,
                })
                state.log_event({"type": "temporary_tag", "minion": t.instance_id,
                                 "tag": tag, "duration": duration})
    else:
        for t in targets:
            if isinstance(t, Minion):
                if tag not in t.tags:
                    t.tags.append(tag)
                if tag == "DIVINE_SHIELD":
                    t.divine_shield = True


@handler("ADD_TAGS")
def _add_tags(state, eff, source_owner, source_minion, ctx):
    tags = eff.get("tags") or []
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    for t in targets:
        if isinstance(t, Minion):
            for tag in tags:
                if tag not in t.tags:
                    t.tags.append(tag)
            if "DIVINE_SHIELD" in tags:
                t.divine_shield = True


@handler("REMOVE_TAG")
def _remove_tag(state, eff, source_owner, source_minion, ctx):
    tag = eff.get("tag")
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    for t in targets:
        if isinstance(t, Minion) and tag in t.tags:
            t.tags.remove(tag)


@handler("SILENCE")
def _silence(state, eff, source_owner, source_minion, ctx):
    """Remove TODO o texto de carta + efeitos do lacaio:
    - Marca silenced=True (impede texto/triggers próprios do lacaio)
    - Limpa estados temporários (divine shield, frozen, cant_attack, immune)
    - REMOVE BUFFS: reseta atk/health para os valores originais da carta
    - Limpa tags/keywords existentes, permitindo buffs futuros normalmente
    """
    from .cards import get_card
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    for t in targets:
        if isinstance(t, Minion):
            t.silenced = True
            t.divine_shield = False
            t.frozen = False
            t.freeze_pending = False
            t.cant_attack = False
            t.immune = False
            # Reseta para os valores ORIGINAIS da carta (remove buffs)
            base = get_card(t.card_id) or {}
            base_atk = base.get("attack") or 0
            base_hp = base.get("health") or 1
            t.attack = base_atk
            # Para health: o lacaio pode ter sido danificado. Mantém o
            # dano mas reseta o teto. Se o lacaio recebeu +X de health,
            # remove esse buff. Se já tinha tomado dano, mantém o dano
            # proporcional (não cura o silenciado).
            damage_taken = max(0, t.max_health - t.health)
            t.max_health = base_hp
            t.health = max(1, base_hp - damage_taken)
            # Remove tags/keywords atuais, inclusive as originais da carta.
            # DORMANT é preservado por precedência de regras: um dormente
            # silenciado continua dormente e não vira um alvo/atacante zumbi.
            preserved_tags = [tag for tag in t.tags if tag == "DORMANT"]
            t.tags = preserved_tags
            state.log_event({"type": "silence", "minion": t.instance_id})


@handler("FREEZE")
def _freeze(state, eff, source_owner, source_minion, ctx):
    """Aplica congelamento. O alvo pula seu próximo turno de ataque.
    
    Mecânica de turnos: marcamos freeze_pending=True no momento do congelamento.
    No fim do turno do dono, freeze_pending vira False (significando que o turno
    está se aproximando). No próximo end_turn do dono (depois de ele ter ficado
    o turno inteiro sem atacar), descongela. Isso garante perda de exatamente
    1 turno de ataque.
    """
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    for t in targets:
        if isinstance(t, Minion):
            t.frozen = True
            # Só marca pending se for congelado por um INIMIGO. Se for o próprio
            # dono congelando o lacaio (raro), aplica imediato.
            t.freeze_pending = (source_owner != t.owner)
            state.log_event({"type": "freeze", "minion": t.instance_id})
        elif isinstance(t, PlayerState):
            t.hero_frozen = True
            t.hero_freeze_pending = (source_owner != t.player_id)
            state.log_event({"type": "freeze_hero", "player": t.player_id})


def _effect_card_id(eff: dict) -> Optional[str]:
    """Extrai card_id de efeitos em formatos usados no cards.json."""
    card_data = eff.get("card") or {}
    return eff.get("card_id") or card_data.get("id")


def _apply_minion_modifications(minion: Minion, modifications: list[dict] | dict | None):
    """Aplica modificações simples numa cópia recém-invocada.

    Aceita tanto a forma antiga `[{action: ...}]` quanto a forma compacta
    usada em algumas cartas: `{"tags": ["TAUNT"]}`.
    """
    if isinstance(modifications, dict):
        compact = []
        for tag in modifications.get("tags") or []:
            compact.append({"action": "ADD_TAG", "tag": tag})
        if "remove_tags" in modifications:
            for tag in modifications.get("remove_tags") or []:
                compact.append({"action": "REMOVE_TAG", "tag": tag})
        if "attack" in modifications or "health" in modifications:
            compact.append({
                "action": "SET_STATS",
                "attack": modifications.get("attack"),
                "health": modifications.get("health"),
            })
        modifications = compact
    for mod in modifications or []:
        if not isinstance(mod, dict):
            continue
        action = mod.get("action")
        if action == "REMOVE_TAG":
            tag = mod.get("tag")
            if tag in minion.tags:
                minion.tags.remove(tag)
            if tag == "DIVINE_SHIELD":
                minion.divine_shield = False
        elif action == "ADD_TAG":
            tag = mod.get("tag")
            if tag and tag not in minion.tags:
                minion.tags.append(tag)
            if tag == "DIVINE_SHIELD":
                minion.divine_shield = True
        elif action == "REMOVE_TRIGGER":
            trigger = mod.get("trigger")
            if trigger:
                minion.effects = [e for e in minion.effects if e.get("trigger") != trigger]
        elif action == "SET_STATS":
            atk = mod.get("attack")
            hp = mod.get("health")
            if atk is not None:
                minion.attack = int(atk)
            if hp is not None:
                minion.health = int(hp)
                minion.max_health = int(hp)


def _grant_tags_to_minion(minion: Minion, tags: list[str] | None):
    for tag in tags or []:
        if tag not in minion.tags:
            minion.tags.append(tag)
        if tag == "DIVINE_SHIELD":
            minion.divine_shield = True


@handler("SUMMON")
def _summon(state, eff, source_owner, source_minion, ctx):
    card_id = _effect_card_id(eff)
    n = eff.get("amount", 1)
    if not card_id:
        return
    owner = source_owner
    dest = eff.get("destination") or eff.get("target") or {}
    if isinstance(dest, dict) and dest.get("mode") in ("OPPONENT_BOARD", "OPPONENT_PLAYER"):
        owner = 1 - source_owner
    for _ in range(n):
        minion = summon_minion_from_card(state, owner, card_id)
        if minion:
            _grant_tags_to_minion(minion, eff.get("granted_tags") or eff.get("tags"))


@handler("SUMMON_TOKEN")
def _summon_token(state, eff, source_owner, source_minion, ctx):
    card_id = _effect_card_id(eff)
    n = eff.get("amount", 1)
    if not card_id:
        return
    for _ in range(n):
        minion = summon_minion_from_card(state, source_owner, card_id)
        if minion:
            _grant_tags_to_minion(minion, eff.get("granted_tags") or eff.get("tags"))


@handler("SUMMON_CARD")
def _summon_card(state, eff, source_owner, source_minion, ctx):
    # Alias usado por algumas cartas. Também permite tags concedidas.
    _summon(state, eff, source_owner, source_minion, ctx)


@handler("SUMMON_AND_ADD_TAGS")
def _summon_and_add_tags(state, eff, source_owner, source_minion, ctx):
    _summon(state, eff, source_owner, source_minion, ctx)


@handler("SUMMON_COPY")
def _summon_copy(state, eff, source_owner, source_minion, ctx):
    # Copia o lacaio fonte, mantendo stats atuais por padrão. Isso evita bugs
    # como Kiwi: a cópia 2/2 deve nascer sem o Último Suspiro após modifications.
    if not source_minion:
        return
    n = eff.get("amount", 1)
    for _ in range(n):
        new_m = summon_minion_from_card(
            state, source_owner, source_minion.card_id,
            stat_override=(source_minion.attack, max(0, source_minion.max_health)),
        )
        if not new_m:
            continue
        new_m.tags = list(source_minion.tags)
        new_m.tribes = list(source_minion.tribes)
        new_m.effects = [dict(e) for e in (source_minion.effects or [])]
        new_m.divine_shield = source_minion.divine_shield or ("DIVINE_SHIELD" in new_m.tags)
        _apply_minion_modifications(new_m, eff.get("modifications"))
        try:
            from .effects_lote17 import register_copy_relationship_for_summon
            register_copy_relationship_for_summon(state, source_minion, new_m)
        except Exception:
            pass
        state.log_event({"type": "summon_copy", "source": source_minion.instance_id,
                         "copy": new_m.instance_id})


@handler("SUMMON_COPY_WITH_STATS")
def _summon_copy_with_stats(state, eff, source_owner, source_minion, ctx):
    target_desc = eff.get("target") or {}
    targets = targeting.resolve_targets(state, target_desc, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    if not targets:
        return
    atk = int(eff.get("attack", 1))
    hp = int(eff.get("health", 1))
    for t in targets:
        if not isinstance(t, Minion):
            continue
        new_m = summon_minion_from_card(state, source_owner, t.card_id,
                                        stat_override=(atk, hp))
        if not new_m:
            # Não interrompe o efeito todo se um único summon falhou (ex: campo
            # cheio em um dos lados): segue para os próximos alvos.
            continue
        # Preserva identidade do minion-fonte: tags, tribos e effects são
        # copiados (sem buffs voláteis em tags _AURA_*) para que a cópia
        # mantenha keywords/tribos/efeitos do original.
        new_m.tags = [tg for tg in (t.tags or []) if not tg.startswith("_AURA_")]
        new_m.tribes = list(t.tribes or [])
        new_m.effects = [dict(e) for e in (t.effects or [])]
        new_m.divine_shield = "DIVINE_SHIELD" in new_m.tags
        _apply_minion_modifications(new_m, eff.get("modifications"))


@handler("SUMMON_SELF_WITH_STAT_MODIFIER")
def _summon_self_with_stat_modifier(state, eff, source_owner, source_minion, ctx):
    if not source_minion:
        return
    atk = max(0, source_minion.attack + int(eff.get("attack_modifier", 0)))
    hp = max(0, source_minion.max_health + int(eff.get("health_modifier", 0)))
    summon_minion_from_card(state, source_owner, source_minion.card_id,
                            stat_override=(atk, hp))


@handler("ADD_CARD_TO_HAND")
def _add_card_hand(state, eff, source_owner, source_minion, ctx):
    # Aceita ambas as formas: top-level "card_id" e nested "card.id".
    # Lotes posteriores (lote26/27) padronizam para card_id; sem este fallback,
    # se o lote27 não fosse o último a registrar, cartas como Cardume falhariam.
    card_data = eff.get("card") or {}
    card_id = eff.get("card_id") or card_data.get("id")
    n = eff.get("amount", 1)
    if not card_id:
        return
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    if not targets:
        targets = [state.player_at(source_owner)]
    for t in targets:
        if isinstance(t, PlayerState):
            for _ in range(n):
                if len(t.hand) < MAX_HAND_SIZE:
                    new_card = CardInHand(instance_id=gen_id("h_"), card_id=card_id)
                    t.hand.append(new_card)


@handler("DISCARD_CARD")
def _discard(state, eff, source_owner, source_minion, ctx):
    n = eff.get("amount", 1)
    p = state.player_at(source_owner)
    if not p.hand:
        return

    # Em partidas reais, descarte é escolha do jogador. Troca Justa e Mundo
    # dos Negócios não devem descartar carta aleatória.
    if getattr(state, "manual_choices", False):
        state.pending_choice = {
            "choice_id": gen_id("choice_"),
            "kind": "discard_hand_card",
            "owner": source_owner,
            "amount": n,
            "cards": [
                {"instance_id": c.instance_id, "card_id": c.card_id}
                for c in p.hand
            ],
        }
        state.log_event({"type": "choice_required", "kind": "discard_hand_card",
                         "player": source_owner})
        return

    chosen = ctx.get("discard_instance_id")
    for _ in range(n):
        if not p.hand:
            return
        if chosen:
            discarded = next((c for c in p.hand if c.instance_id == chosen), None)
            chosen = None
            if discarded is None:
                return
            p.hand.remove(discarded)
        else:
            idx = state.rng.randrange(len(p.hand))
            discarded = p.hand.pop(idx)
        state.log_event({"type": "discard", "player": p.player_id,
                         "instance_id": discarded.instance_id,
                         "card_id": discarded.card_id})


@handler("GAIN_TEMP_MANA")
def _gain_temp_mana(state, eff, source_owner, source_minion, ctx):
    amount = eff.get("amount", 1)
    p = state.player_at(source_owner)
    p.mana = min(p.mana + amount, 10)


@handler("SHUFFLE_DECK")
def _shuffle(state, eff, source_owner, source_minion, ctx):
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    if not targets:
        targets = [state.player_at(source_owner)]
    for t in targets:
        if isinstance(t, PlayerState):
            state.rng.shuffle(t.deck)


@handler("CONDITIONAL_EFFECTS")
def _conditional(state, eff, source_owner, source_minion, ctx):
    """Aplica efeitos aninhados se a condição for satisfeita."""
    cond = eff.get("condition") or {}
    if not check_condition(state, cond, source_owner, source_minion, ctx):
        return
    sub_effects = list(eff.get("effects") or [])
    for i, sub in enumerate(sub_effects):
        resolve_effect(state, sub, source_owner, source_minion, ctx)
        if state.pending_choice is not None:
            attach_resume_to_pending_choice(state, sub_effects[i + 1:],
                                            source_owner, source_minion, ctx)
            break


@handler("SEQUENCE")
def _sequence(state, eff, source_owner, source_minion, ctx):
    sub_effects = list(eff.get("effects") or [])
    for i, sub in enumerate(sub_effects):
        resolve_effect(state, sub, source_owner, source_minion, ctx)
        if state.pending_choice is not None:
            attach_resume_to_pending_choice(state, sub_effects[i + 1:],
                                            source_owner, source_minion, ctx)
            break


def check_condition(state, cond: dict, source_owner: int,
                    source_minion: Optional[Minion], ctx: Optional[dict] = None) -> bool:
    """Avalia condições usadas por CONDITIONAL_EFFECTS.

    Condições desconhecidas retornam False, mas agora são registradas no log da
    partida para não falharem silenciosamente durante testes/jogos.
    """
    ctx = ctx or {}
    ctype = cond.get("type")
    me = state.player_at(source_owner)
    foe = state.opponent_of(source_owner)

    def chosen_minion():
        tid = ctx.get("chosen_target")
        if not tid:
            return None
        found = state.find_minion(tid)
        return found[0] if found else None

    if ctype == "FRIENDLY_MINION_TRIBE_EXISTS":
        tribe = cond.get("tribe")
        exclude_self = cond.get("exclude_self", False)
        for m in me.board:
            if exclude_self and m is source_minion:
                continue
            if tribe and m.has_tribe(tribe):
                return True
        return False
    if ctype in ("FRIENDLY_MINION_COUNT_GTE", "FRIENDLY_MINION_COUNT_AT_LEAST"):
        board = list(me.board)
        if cond.get("exclude_self") and source_minion is not None:
            board = [m for m in board if m is not source_minion]
        return len(board) >= cond.get("amount", cond.get("count", 0))
    if ctype == "ENEMY_MINION_COUNT_GTE":
        return len(foe.board) >= cond.get("amount", cond.get("count", 0))
    if ctype == "HAND_SIZE_GTE":
        return len(me.hand) >= cond.get("amount", 0)
    if ctype == "FRIENDLY_MINION_EXISTS":
        return any(m is not source_minion for m in me.board) if cond.get("exclude_self") else bool(me.board)
    if ctype == "ENEMY_MINION_EXISTS":
        return bool(foe.board)
    if ctype == "TARGET_HAS_TRIBE":
        m = chosen_minion()
        tribe = cond.get("tribe")
        return bool(m and tribe and m.has_tribe(tribe))
    if ctype == "TARGET_IS_FROZEN":
        m = chosen_minion()
        return bool(m and m.frozen)
    if ctype == "TARGET_ATTACK_LESS_THAN_SELF_ATTACK":
        m = chosen_minion()
        return bool(m and source_minion and m.attack < source_minion.attack)
    if ctype == "ONLY_FRIENDLY_MINION":
        return source_minion is not None and len(me.board) == 1 and me.board[0] is source_minion
    if ctype in ("PLAYED_CARD_TRIBE", "CARD_TRIBE", "SUMMONED_MINION_TRIBE"):
        tribe = cond.get("tribe")
        card_id = ctx.get("played_card_id") or ctx.get("source_card_id")
        played_minion_id = ctx.get("played_minion")
        if played_minion_id:
            found = state.find_minion(played_minion_id)
            if found and tribe:
                return found[0].has_tribe(tribe)
        if card_id and tribe:
            from .cards import get_card, card_has_tribe
            return card_has_tribe(get_card(card_id), tribe)
        return False
    if ctype == "OPPONENT_HAS_MORE_CARDS_IN_HAND":
        return len(foe.hand) > len(me.hand)
    if ctype == "SELF_DAMAGED":
        return bool(source_minion and source_minion.health < source_minion.max_health)
    if ctype == "ATTACKED_ENEMY":
        return bool(ctx.get("attack_target_owner") is not None and ctx.get("attack_target_owner") != source_owner)
    if ctype == "DIED_DURING_OPPONENT_TURN":
        return state.current_player != source_owner
    if ctype == "SELF_COULD_ATTACK_BUT_DID_NOT":
        return bool(source_minion and source_minion.can_attack() and source_minion.attacks_this_turn == 0)

    state.log_event({"type": "unimplemented_condition", "condition": ctype})
    return False



def _safe_resume_ctx(ctx: dict) -> dict:
    """Copia apenas valores simples do contexto para retomada pós-escolha."""
    allowed = {}
    for k, v in (ctx or {}).items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            allowed[k] = v
        elif isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) or x is None for x in v):
            allowed[k] = list(v)
    return allowed


def attach_resume_to_pending_choice(state: GameState, remaining_effects: list[dict],
                                    source_owner: int,
                                    source_minion: Optional[Minion],
                                    ctx: dict):
    """Anexa continuação de efeitos à escolha pendente atual."""
    if state.pending_choice is None or not remaining_effects:
        return
    state.pending_choice["resume"] = {
        "kind": "effects",
        "effects": list(remaining_effects),
        "source_owner": source_owner,
        "source_minion_id": source_minion.instance_id if source_minion else None,
        "ctx": _safe_resume_ctx(ctx),
    }



# ======================= RESOLVE GENÉRICO =======================

def _prepare_effect_context(eff: dict, ctx: dict) -> dict:
    """Prepara ctx['chosen_target'] para efeitos CHOSEN sequenciais.

    Backward-compatible: se não houver target_queue, usa o chosen_target antigo.
    Com target_queue, cada efeito CHOSEN consome o próximo alvo. Efeitos como
    SAME_AS_PREVIOUS_TARGET reaproveitam o último alvo consumido.
    """
    target = eff.get("target") or {}
    mode = target.get("mode") if isinstance(target, dict) else None

    if mode == "CHOSEN":
        queue = ctx.get("target_queue")
        if isinstance(queue, list):
            cursor = int(ctx.get("target_cursor") or 0)
            if cursor < len(queue):
                ctx["chosen_target"] = queue[cursor]
                ctx["previous_target"] = queue[cursor]
                ctx["target_cursor"] = cursor + 1
        elif ctx.get("chosen_target") is not None:
            ctx["previous_target"] = ctx.get("chosen_target")
    elif mode in ("SAME_AS_PREVIOUS_TARGET", "SAME_AS_PREVIOUS"):
        if ctx.get("previous_target") is not None:
            ctx["chosen_target"] = ctx.get("previous_target")
    return ctx


def resolve_effect(state: GameState, eff: dict, source_owner: int,
                   source_minion: Optional[Minion], ctx: dict):
    """Resolve um efeito (dict com 'action')."""
    if state.pending_choice is not None:
        return
    action = eff.get("action")
    if not action:
        return
    h = HANDLERS.get(action)
    if h is None:
        UNIMPLEMENTED_ACTIONS.add(action)
        state.log_event({"type": "unimplemented_action", "action": action})
        return
    ctx = _prepare_effect_context(eff, ctx)
    previous_trigger = getattr(targeting, "CURRENT_SOURCE_TRIGGER", None)
    targeting.CURRENT_SOURCE_TRIGGER = ctx.get("source_trigger")
    try:
        h(state, eff, source_owner, source_minion, ctx)
    finally:
        targeting.CURRENT_SOURCE_TRIGGER = previous_trigger


def resolve_card_effects(state: GameState, card: dict, source_owner: int,
                         trigger: str, source_minion: Optional[Minion] = None,
                         chosen_target: Optional[str] = None,
                         is_spell: bool = False,
                         extra_ctx: Optional[dict] = None):
    """Resolve todos os efeitos da carta que correspondem ao trigger dado."""
    ctx = {"chosen_target": chosen_target, "is_spell": is_spell, "source_trigger": trigger}
    if extra_ctx:
        ctx.update(extra_ctx)
    trigger_effects = [eff for eff in (card.get("effects") or []) if eff.get("trigger") == trigger]
    for i, eff in enumerate(trigger_effects):
        resolve_effect(state, eff, source_owner, source_minion, ctx)
        if state.pending_choice is not None:
            attach_resume_to_pending_choice(state, trigger_effects[i + 1:],
                                            source_owner, source_minion, ctx)
            break


def fire_minion_trigger(state: GameState, minion: Minion, trigger: str,
                        extra_ctx: Optional[dict] = None):
    """Dispara triggers passivos de um lacaio (ex: ON_DEATH, AFTER_FRIENDLY_MINION_PLAY)."""
    if minion.silenced:
        return
    ctx = {"chosen_target": None, "is_spell": False, "source_trigger": trigger}
    if extra_ctx:
        ctx.update(extra_ctx)
    for eff in minion.effects:
        if eff.get("trigger") == trigger:
            cond = eff.get("condition") or {}
            if isinstance(cond, dict) and cond.get("type"):
                if not check_condition(state, cond, minion.owner, minion, ctx):
                    continue
            resolve_effect(state, eff, minion.owner, minion, ctx)
            if state.pending_choice is not None:
                break


@handler("CANNOT_BE_TARGETED_BY_SPELLS")
def _cannot_be_targeted_by_spells(state, eff, source_owner, source_minion, ctx):
    """Marca lacaios como inalvejáveis por qualquer feitiço escolhido."""
    targets = targeting.resolve_targets(state, eff.get("target") or {"mode": "SELF"},
                                        source_owner, source_minion, ctx.get("chosen_target"))
    for t in targets:
        if isinstance(t, Minion) and "SPELL_TARGET_IMMUNITY" not in t.tags:
            t.tags.append("SPELL_TARGET_IMMUNITY")
            state.log_event({"type": "spell_target_immunity", "minion": t.instance_id})


@handler("CANNOT_BE_TARGETED_BY_ENEMY_SPELLS")
def _cannot_be_targeted_by_enemy_spells(state, eff, source_owner, source_minion, ctx):
    """Marca lacaios como alvo válido apenas para feitiços do próprio dono."""
    targets = targeting.resolve_targets(state, eff.get("target") or {"mode": "SELF"},
                                        source_owner, source_minion, ctx.get("chosen_target"))
    for t in targets:
        if isinstance(t, Minion) and "FRIENDLY_SPELL_TARGET_ONLY" not in t.tags:
            t.tags.append("FRIENDLY_SPELL_TARGET_ONLY")
            state.log_event({"type": "enemy_spell_target_immunity", "minion": t.instance_id})


@handler("GRANT_TEMPORARY_SPELL_TARGET_IMMUNITY")
def _grant_temporary_spell_target_immunity(state, eff, source_owner, source_minion, ctx):
    """Zé Droguinha: aliados não podem ser alvejados por feitiços inimigos
    até o fim do próximo turno do oponente.
    """
    targets = targeting.resolve_targets(state, eff.get("target") or {"mode": "FRIENDLY_CHARACTERS"},
                                        source_owner, source_minion, ctx.get("chosen_target"))
    remove_owner = 1 - source_owner if eff.get("duration") == "UNTIL_NEXT_TURN_END" else source_owner
    for t in targets:
        if isinstance(t, Minion):
            tag = "ENEMY_SPELL_TARGET_IMMUNITY"
            if tag not in t.tags:
                t.tags.append(tag)
            state.pending_modifiers.append({
                "kind": "temporary_spell_target_immunity",
                "owner": source_owner,
                "remove_on_turn_owner": remove_owner,
                "target_kind": "minion",
                "target_id": t.instance_id,
                "tag": tag,
            })
            state.log_event({"type": "temporary_spell_target_immunity",
                             "target_kind": "minion", "target_id": t.instance_id})
        elif isinstance(t, PlayerState):
            t.hero_spell_target_immune = True
            state.pending_modifiers.append({
                "kind": "temporary_spell_target_immunity",
                "owner": source_owner,
                "remove_on_turn_owner": remove_owner,
                "target_kind": "hero",
                "target_id": t.player_id,
            })
            state.log_event({"type": "temporary_spell_target_immunity",
                             "target_kind": "hero", "target_id": t.player_id})


@handler("PREVENT_ATTACK_AGAINST_SELF")
def _prevent_attack_against_self(state, eff, source_owner, source_minion, ctx):
    """El Luca: o alvo escolhido não pode atacar este lacaio."""
    if source_minion is None:
        return
    attackers = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                          source_minion, ctx.get("chosen_target"))
    for t in attackers:
        if isinstance(t, Minion):
            state.pending_modifiers.append({
                "kind": "cannot_attack_target",
                "attacker_id": t.instance_id,
                "target_id": source_minion.instance_id,
                "source_minion_id": source_minion.instance_id,
            })
            state.log_event({"type": "cannot_attack_target",
                             "attacker": t.instance_id,
                             "target": source_minion.instance_id})


@handler("PREVENT_DAMAGE_THIS_TURN")
def _prevent_damage_this_turn(state, eff, source_owner, source_minion, ctx):
    """O alvo fica incapaz de causar dano até o fim do turno atual."""
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    for t in targets:
        if isinstance(t, Minion):
            tag = "CANNOT_DEAL_DAMAGE_THIS_TURN"
            if tag not in t.tags:
                t.tags.append(tag)
            state.pending_modifiers.append({
                "kind": "temporary_tag",
                "owner": source_owner,
                "minion_id": t.instance_id,
                "tag": tag,
                "remove_at": "end_of_turn",
                "added_now": True,
            })
            state.log_event({"type": "cannot_deal_damage_this_turn", "minion": t.instance_id})


@handler("REDIRECT_ATTACK_TO_SELF")
def _redirect_attack_to_self(state, eff, source_owner, source_minion, ctx):
    """Handler marcador para Lucas. A engine de ataque lê o efeito direto
    para redirecionar o alvo; aqui só registra o estado visual/log se o trigger
    for disparado manualmente.
    """
    if source_minion and "REDIRECT_ATTACK_TO_SELF" not in source_minion.tags:
        source_minion.tags.append("REDIRECT_ATTACK_TO_SELF")
        state.log_event({"type": "redirect_attack_to_self_ready",
                         "minion": source_minion.instance_id})


@handler("SKIP_NEXT_ATTACK")
def _skip_next_attack(state, eff, source_owner, source_minion, ctx):
    targets = targeting.resolve_targets(state, eff.get("target") or {}, source_owner,
                                        source_minion, ctx.get("chosen_target"))
    for t in targets:
        if isinstance(t, Minion):
            t.skip_next_attack = True
            state.log_event({"type": "skip_next_attack", "minion": t.instance_id})


# Registra os handlers dos lotes (módulos separados pra manter este arquivo enxuto)
from . import effects_lote1
effects_lote1.register_lote1_handlers(handler)

from . import effects_lote2
effects_lote2.register_lote2_handlers(handler)

from . import effects_lote3_familia1
effects_lote3_familia1.register_familia1_handlers(handler)

from . import effects_lote9
effects_lote9.register_lote9_handlers(handler)

from . import effects_lote10
effects_lote10.register_lote10_handlers(handler)

from . import effects_lote13
effects_lote13.register_lote13_handlers(handler)

from . import effects_lote14
effects_lote14.register_lote14_handlers(handler)

from . import effects_lote15
effects_lote15.register_lote15_handlers(handler)

from . import effects_lote16
effects_lote16.register_lote16_handlers(handler)

from . import effects_lote17
effects_lote17.register_lote17_handlers(handler)

from . import effects_lote19
effects_lote19.register_lote19_handlers(handler)

from . import effects_lote22_bugfix
effects_lote22_bugfix.register_lote22_bugfix_handlers(handler)

from . import effects_lote24_second_audit
effects_lote24_second_audit.register_lote24_second_audit_handlers(handler)

from . import effects_lote25_requested_fixes
effects_lote25_requested_fixes.register_lote25_requested_fixes_handlers(handler)

from . import effects_lote26_requested_fixes
effects_lote26_requested_fixes.register_lote26_requested_fixes_handlers(handler)

from . import effects_lote27_requested_fixes
effects_lote27_requested_fixes.register_lote27_requested_fixes_handlers(handler)

from . import effects_lote29_features
effects_lote29_features.register_lote29_features_handlers(handler)

from . import effects_lote33_requested_cards
effects_lote33_requested_cards.register_lote33_requested_cards_handlers(handler)
