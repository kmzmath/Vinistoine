"""
Sistema de targeting. Resolve descrições de "target" em listas de entidades concretas.

A regra principal é simples: o cliente pode sugerir um alvo, mas quem decide se
esse alvo é legal é sempre o servidor.
"""
from __future__ import annotations
from typing import Optional
from .state import GameState, Minion, PlayerState

CURRENT_SOURCE_TRIGGER: str | None = None



def _is_trigger_immune(candidate, source_owner: int) -> bool:
    """Bloqueia alvos imunes ao trigger atualmente resolvido.

    Usado para Surdo: imune a efeitos ON_PLAY. Isso também filtra AOE de
    battlecry, não apenas CHOSEN.
    """
    if not isinstance(candidate, Minion):
        return False
    trigger = CURRENT_SOURCE_TRIGGER
    if not trigger:
        return False
    return candidate.has_tag(f"TRIGGER_IMMUNE_{trigger}")


def _passes_extra_filters(target_desc: dict, candidate, source_owner: int,
                          source_minion: Optional[Minion] = None) -> bool:
    """Filtros adicionais usados pelo JSON além de target.valid."""
    if isinstance(candidate, Minion):
        if candidate.has_tag("DORMANT"):
            return False
        required_tribe = target_desc.get("required_tribe") or target_desc.get("tribe")
        required_tribes = target_desc.get("tribes") or []
        if isinstance(required_tribes, str):
            required_tribes = [required_tribes]
        if required_tribe and required_tribe not in required_tribes:
            required_tribes.append(required_tribe)
        if required_tribes and not any(candidate.has_tribe(t) for t in required_tribes):
            return False
        required_card_ids = target_desc.get("required_card_ids") or target_desc.get("card_ids") or []
        if isinstance(required_card_ids, str):
            required_card_ids = [required_card_ids]
        if required_card_ids and candidate.card_id not in required_card_ids:
            return False
        card_id_prefix = target_desc.get("card_id_prefix")
        if card_id_prefix and not str(candidate.card_id).startswith(str(card_id_prefix)):
            return False
        required_tag = target_desc.get("required_tag")
        if required_tag and not candidate.has_tag(required_tag):
            return False
        exclude_self = target_desc.get("exclude_self", False)
        if exclude_self and source_minion is not None and candidate is source_minion:
            return False
    return True


def is_valid_target(state: GameState, target_desc: dict, candidate, source_owner: int,
                    source_minion: Optional[Minion] = None,
                    is_spell: bool = False) -> bool:
    """Verifica se um candidato concreto satisfaz os filtros do target."""
    # FILTROS GLOBAIS: stealth e immune (independem do filtro 'valid')
    if isinstance(candidate, Minion):
        if candidate.has_tag("DORMANT"):
            return False
        if _is_trigger_immune(candidate, source_owner):
            return False
        # Inimigo com STEALTH não pode ser alvo escolhido por feitiços/battlecries.
        # AOE não passa por CHOSEN, então não é bloqueado por isso.
        if candidate.owner != source_owner and candidate.has_tag("STEALTH"):
            return False
        if candidate.immune:
            return False
        # Restrições de targeting por feitiços. Só afetam alvos escolhidos;
        # AOE usa outros modes e continua passando normalmente.
        if is_spell:
            if candidate.has_tag("SPELL_TARGET_IMMUNITY"):
                return False
            if candidate.owner != source_owner and (
                candidate.has_tag("ENEMY_SPELL_TARGET_IMMUNITY")
                or candidate.has_tag("FRIENDLY_SPELL_TARGET_ONLY")
            ):
                return False
    elif isinstance(candidate, PlayerState):
        if candidate.player_id != source_owner and candidate.hero_immune:
            return False
        if is_spell and candidate.player_id != source_owner and getattr(candidate, "hero_spell_target_immune", False):
            return False
    else:
        return False

    if not _passes_extra_filters(target_desc, candidate, source_owner, source_minion):
        return False

    valid = target_desc.get("valid") or []
    if not valid:
        return True

    if isinstance(candidate, Minion):
        m = candidate
        for v in valid:
            if v == "FRIENDLY_MINION" and m.owner == source_owner:
                return True
            if v == "ENEMY_MINION" and m.owner != source_owner:
                return True
            if v in ("ANY_MINION", "MINION", "ANY_CHARACTER"):
                return True
            if v == "OTHER_FRIENDLY_MINION" and m.owner == source_owner and m is not source_minion:
                return True
            if v == "FRIENDLY_CHARACTER" and m.owner == source_owner:
                return True
        return False

    if isinstance(candidate, PlayerState):
        for v in valid:
            if v == "FRIENDLY_HERO" and candidate.player_id == source_owner:
                return True
            if v == "ENEMY_HERO" and candidate.player_id != source_owner:
                return True
            if v == "FRIENDLY_CHARACTER" and candidate.player_id == source_owner:
                return True
            if v == "ENEMY_CHARACTER" and candidate.player_id != source_owner:
                return True
            if v in ("ANY_HERO", "ANY_CHARACTER"):
                return True
            if v == "FRIENDLY_CHARACTER" and candidate.player_id == source_owner:
                return True
        return False

    return False


def _target_by_id(state: GameState, target_id: Optional[str], source_owner: int,
                  target_desc: dict, source_minion: Optional[Minion],
                  is_spell: bool = False) -> list:
    if target_id is None:
        return []
    found = state.find_minion(target_id)
    if found:
        m, _ = found
        return [m] if is_valid_target(state, target_desc, m, source_owner, source_minion, is_spell=is_spell) else []
    me = state.player_at(source_owner)
    foe = state.opponent_of(source_owner)
    if target_id == f"hero:{me.player_id}":
        return [me] if is_valid_target(state, target_desc, me, source_owner, source_minion, is_spell=is_spell) else []
    if target_id == f"hero:{foe.player_id}":
        return [foe] if is_valid_target(state, target_desc, foe, source_owner, source_minion, is_spell=is_spell) else []
    return []


def _filter_minions(target_desc: dict, minions: list[Minion], source_owner: int,
                    source_minion: Optional[Minion]) -> list[Minion]:
    return [
        m for m in minions
        if not _is_trigger_immune(m, source_owner)
        and _passes_extra_filters(target_desc, m, source_owner, source_minion)
    ]


def resolve_targets(state: GameState, target_desc: dict, source_owner: int,
                    source_minion: Optional[Minion] = None,
                    chosen_target_id: Optional[str] = None,
                    is_spell: bool = False) -> list:
    """
    Retorna lista de entidades alvo (Minion ou PlayerState).
    Para CHOSEN, usa chosen_target_id que vem do cliente.
    """
    if not target_desc:
        return []
    mode = target_desc.get("mode", "")
    me = state.player_at(source_owner)
    foe = state.opponent_of(source_owner)

    if mode == "SELF":
        return [source_minion] if source_minion else []
    if mode in ("SELF_PLAYER", "SELF_HERO"):
        return [me]
    if mode in ("OPPONENT_PLAYER", "OPPONENT_HERO"):
        return [foe]
    if mode in ("ALL_FRIENDLY_MINIONS", "FRIENDLY_MINIONS"):
        return _filter_minions(target_desc, list(me.board), source_owner, source_minion)
    if mode in ("ALL_OTHER_FRIENDLY_MINIONS", "OTHER_FRIENDLY_MINIONS"):
        return _filter_minions(target_desc, [m for m in me.board if m is not source_minion], source_owner, source_minion)
    if mode in ("ALL_ENEMY_MINIONS", "ENEMY_MINIONS"):
        return _filter_minions(target_desc, list(foe.board), source_owner, source_minion)
    if mode == "ALL_MINIONS":
        return _filter_minions(target_desc, list(me.board) + list(foe.board), source_owner, source_minion)
    if mode == "ALL_OTHER_MINIONS":
        return _filter_minions(target_desc, [m for m in me.board if m is not source_minion] + list(foe.board), source_owner, source_minion)
    if mode == "ALL_MINIONS_EXCEPT_CHOSEN":
        return _filter_minions(target_desc, [m for m in state.all_minions() if m.instance_id != chosen_target_id], source_owner, source_minion)
    if mode == "ALL_CHARACTERS":
        return [me, foe] + _filter_minions(target_desc, list(me.board) + list(foe.board), source_owner, source_minion)
    if mode == "ALL_OTHER_CHARACTERS":
        return [me, foe] + _filter_minions(target_desc, [m for m in (me.board + foe.board) if m is not source_minion], source_owner, source_minion)
    if mode == "ALL_ENEMIES":
        return [foe] + _filter_minions(target_desc, list(foe.board), source_owner, source_minion)
    if mode == "FRIENDLY_CHARACTERS":
        return [me] + _filter_minions(target_desc, list(me.board), source_owner, source_minion)
    if mode == "SELF_BOARD":
        return _filter_minions(target_desc, list(me.board), source_owner, source_minion)
    if mode == "MINIONS_WITH_TRIBE":
        tribe = target_desc.get("tribe") or target_desc.get("required_tribe")
        pool = [m for m in state.all_minions() if not tribe or m.has_tribe(tribe)]
        return _filter_minions(target_desc, pool, source_owner, source_minion)
    if mode == "ALL_MINIONS_EXCEPT_TRIBE":
        tribe = target_desc.get("tribe") or target_desc.get("excluded_tribe")
        pool = [m for m in state.all_minions() if tribe and not m.has_tribe(tribe)]
        # Para os modos "EXCEPT_TRIBE", o campo `tribe` significa "exclua esta
        # tribo" - NÃO um required_tribe. Removemos do desc para que
        # _passes_extra_filters não filtre adicionalmente exigindo a tribo.
        clean_desc = {k: v for k, v in target_desc.items() if k not in ("tribe", "required_tribe")}
        return _filter_minions(clean_desc, pool, source_owner, source_minion)
    if mode == "ENEMY_MINIONS_EXCEPT_TRIBE":
        tribe = target_desc.get("tribe") or target_desc.get("excluded_tribe")
        pool = [m for m in foe.board if tribe and not m.has_tribe(tribe)]
        clean_desc = {k: v for k, v in target_desc.items() if k not in ("tribe", "required_tribe")}
        return _filter_minions(clean_desc, pool, source_owner, source_minion)
    if mode == "PLAYED_MINION":
        played_id = target_desc.get("id") or chosen_target_id
        return _target_by_id(state, played_id, source_owner, target_desc, source_minion, is_spell=is_spell) if played_id else []
    if mode == "DAMAGE_SOURCE":
        source_id = (target_desc.get("id") or chosen_target_id)
        return _target_by_id(state, source_id, source_owner, target_desc, source_minion, is_spell=is_spell) if source_id else []
    if mode == "DAMAGED_MINION":
        damaged_id = target_desc.get("id") or chosen_target_id
        if damaged_id:
            return _target_by_id(state, damaged_id, source_owner, target_desc, source_minion, is_spell=is_spell)
        pool = [m for m in state.all_minions() if m.health < m.max_health]
        return _filter_minions(target_desc, pool, source_owner, source_minion)

    # Reusa o alvo escolhido como "alvo anterior". Isso cobre cartas como
    # Mamma Mia, que escolhem um alvo e depois aplicam condição/buff no mesmo.
    if mode in ("CHOSEN", "CHOSEN_EACH", "CHOSEN_ADJACENT_DIRECTION", "SAME_AS_PREVIOUS_TARGET", "SAME_AS_PREVIOUS", "REVEALED_CARD"):
        return _target_by_id(state, chosen_target_id, source_owner, target_desc, source_minion, is_spell=is_spell)

    # === Adjacência ===
    if mode in ("ADJACENT_MINIONS", "ADJACENT_FRIENDLY_MINIONS",
                "ADJACENT_TO_PREVIOUS_TARGET", "ADJACENT_TO_CHOSEN_MINION",
                "ADJACENT_TO_ATTACK_TARGET"):
        pivot = None
        if chosen_target_id:
            f = state.find_minion(chosen_target_id)
            if f:
                pivot = f[0]
        if pivot is None:
            pivot = source_minion
        if pivot is None:
            return []
        owner_board = state.player_at(pivot.owner).board
        try:
            idx = owner_board.index(pivot)
        except ValueError:
            return []
        out = []
        if idx > 0:
            out.append(owner_board[idx - 1])
        if idx < len(owner_board) - 1:
            out.append(owner_board[idx + 1])
        if mode == "ADJACENT_FRIENDLY_MINIONS":
            out = [m for m in out if m.owner == source_owner]
        return _filter_minions(target_desc, out, source_owner, source_minion)

    if mode == "RANDOM_ENEMY_MINION":
        pool = _filter_minions(target_desc, list(foe.board), source_owner, source_minion)
        return [state.rng.choice(pool)] if pool else []
    if mode == "RANDOM_FRIENDLY_MINION":
        pool = _filter_minions(target_desc, [m for m in me.board if m is not source_minion], source_owner, source_minion)
        return [state.rng.choice(pool)] if pool else []
    if mode == "RANDOM_MINION":
        pool = _filter_minions(target_desc, state.all_minions(), source_owner, source_minion)
        return [state.rng.choice(pool)] if pool else []
    if mode == "RANDOM_ENEMY_CHARACTER":
        # Filtra herói imune: alvo aleatório não deve "queimar" em alvo intocável.
        hero_pool = []
        if not foe.hero_immune and not (is_spell and getattr(foe, "hero_spell_target_immune", False)):
            hero_pool = [foe]
        pool = hero_pool + _filter_minions(target_desc, list(foe.board), source_owner, source_minion)
        return [state.rng.choice(pool)] if pool else []

    # Não tratados aqui: SELF_DECK, SELF_HAND, BOTH_DECKS etc são tratados pelas
    # próprias actions, pois não retornam Minion/PlayerState.
    return []


def _chosen_targets_in_effect(eff: dict, chose_index: Optional[int] = None) -> list[dict]:
    """Encontra targets CHOSEN em estruturas de efeito.

    Para CHOOSE_ONE, considera apenas a opção escolhida quando `chose_index`
    é informado. Isso evita exigir alvos de opções que o jogador não escolheu.
    """
    out: list[dict] = []
    if not isinstance(eff, dict):
        return out

    if eff.get("action") == "CHOOSE_ONE":
        choices = eff.get("choices") or []
        if not choices:
            return out
        idx = chose_index if chose_index is not None else 0
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = 0
        idx = max(0, min(idx, len(choices) - 1))
        return _chosen_targets_in_effect(choices[idx], chose_index=chose_index)

    # Alguns efeitos possuem dois alvos conceituais: `source` (o lacaio
    # sacrificado/consumido) e `target` (o alvo final). Ambos precisam entrar
    # na validação prévia de play_card, na ordem em que o cliente deve enviar
    # `targets`.
    src = eff.get("source") or {}
    if isinstance(src, dict) and src.get("mode") == "CHOSEN":
        out.append(src)

    # `excluded_target`: usado por cartas como Blitz, onde o jogador escolhe
    # um lacaio para POUPAR (ele é o alvo escolhido, mas não recebe o efeito;
    # os demais sim). Sem este registro, a UI não pediria alvo e a carta
    # afetaria todos os lacaios indiscriminadamente.
    excluded = eff.get("excluded_target") or {}
    if isinstance(excluded, dict) and excluded.get("mode") == "CHOSEN":
        out.append(excluded)

    tgt = eff.get("target") or {}
    if isinstance(tgt, dict):
        mode = tgt.get("mode")
        if mode == "CHOSEN":
            out.append(tgt)
        elif mode == "CHOSEN_EACH":
            # Uma escolha por etapa da sequência. Usa o tamanho de amounts
            # quando disponível; fallback para 1 para manter compatibilidade.
            n = len(eff.get("amounts") or []) or int(eff.get("amount", 1) or 1)
            for _ in range(max(1, n)):
                desc = dict(tgt)
                desc["mode"] = "CHOSEN"
                out.append(desc)
        elif mode == "CHOSEN_ADJACENT_DIRECTION":
            desc = dict(tgt)
            desc["mode"] = "CHOSEN"
            out.append(desc)

    for key in ("effects", "additional_effects"):
        for sub in eff.get(key) or []:
            out.extend(_chosen_targets_in_effect(sub, chose_index=chose_index))
    return out


def chosen_targets_for_card(card: dict, chose_index: Optional[int] = None,
                            triggers: Optional[list[str] | tuple[str, ...] | set[str]] = None) -> list[dict]:
    """Retorna todos os targets CHOSEN relevantes para jogar a carta.

    Por padrão cobre ON_PLAY. A engine pode passar triggers alternativos para
    Combo/Fortalecer.
    """
    if triggers is None:
        triggers = ("ON_PLAY",)
    triggers = set(triggers)
    out: list[dict] = []
    for eff in card.get("effects") or []:
        if eff.get("trigger") not in triggers:
            continue
        out.extend(_chosen_targets_in_effect(eff, chose_index=chose_index))
    return out


def needs_chosen_target(card: dict) -> Optional[dict]:
    """Compatibilidade: retorna o primeiro alvo escolhido da carta, se existir."""
    targets = chosen_targets_for_card(card)
    return targets[0] if targets else None

def has_valid_chosen_target(state: GameState, target_desc: dict, source_owner: int,
                            source_minion: Optional[Minion] = None,
                            is_spell: bool = False) -> bool:
    """Existe ao menos um alvo legal pra esse target_desc?"""
    me = state.player_at(source_owner)
    foe = state.opponent_of(source_owner)
    valid = target_desc.get("valid") or []

    candidates = []
    if not valid:
        candidates.extend([me, foe])
        candidates.extend(state.all_minions())
    for v in valid:
        if v == "FRIENDLY_MINION":
            candidates.extend(me.board)
        elif v == "ENEMY_MINION":
            candidates.extend(foe.board)
        elif v in ("ANY_MINION", "MINION"):
            candidates.extend(state.all_minions())
        elif v == "FRIENDLY_HERO":
            candidates.append(me)
        elif v == "ENEMY_HERO":
            candidates.append(foe)
        elif v in ("ANY_HERO", "ANY_CHARACTER"):
            candidates.extend([me, foe])
            if v == "ANY_CHARACTER":
                candidates.extend(state.all_minions())
        elif v == "OTHER_FRIENDLY_MINION":
            candidates.extend([m for m in me.board if m is not source_minion])
        elif v == "FRIENDLY_CHARACTER":
            candidates.extend([me] + list(me.board))

    seen = set()
    unique = []
    for c in candidates:
        key = id(c)
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return any(is_valid_target(state, target_desc, c, source_owner, source_minion, is_spell=is_spell) for c in unique)
