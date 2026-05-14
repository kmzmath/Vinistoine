"""
Estado do jogo: classes que representam o mundo do jogo em um momento específico.
Isso é o "modelo" puro - sem lógica de regras complexa.
"""
from __future__ import annotations
import random
import uuid
from dataclasses import dataclass, field
from typing import Optional


# ======================== CONSTANTES ========================
STARTING_HEALTH = 30
MAX_MANA = 10
MAX_HAND_SIZE = 10
MAX_BOARD_SIZE = 7
DECK_SIZE = 30
STARTING_HAND_FIRST = 3
STARTING_HAND_SECOND = 4  # segundo jogador compra 1 a mais (fairness)
FATIGUE_START = 1  # dano por fadiga começa em 1


@dataclass
class Minion:
    """Lacaio em campo. Cada instância tem um id único pra referenciar via rede."""
    instance_id: str
    card_id: str
    name: str
    attack: int
    health: int
    max_health: int
    tags: list[str] = field(default_factory=list)
    tribes: list[str] = field(default_factory=list)
    effects: list[dict] = field(default_factory=list)
    # Estado durante o turno
    attacks_this_turn: int = 0
    summoning_sick: bool = True  # acabou de entrar em campo, não pode atacar
    divine_shield: bool = False
    frozen: bool = False
    # Marcador histórico usado por alguns efeitos de congelamento sustentado.
    # A regra principal do congelamento é consumida pela próxima oportunidade
    # real de ataque (ver engine._would_be_able_to_attack_without_attack_skip).
    freeze_pending: bool = False
    silenced: bool = False
    cant_attack: bool = False
    immune: bool = False
    # SKIP_NEXT_ATTACK: perde a próxima oportunidade real de ataque; só é
    # limpo quando o lacaio teria conseguido atacar sem esta restrição.
    skip_next_attack: bool = False
    # Habilidades ativadas / ações especiais de "durante seu turno".
    # Por padrão: uma ativação por turno enquanto o lacaio estiver em campo.
    activated_abilities_this_turn: int = 0
    # Usos totais restantes por índice de habilidade ativada. Usado por cartas
    # como Ramoninho Mestre da Nerf: o número entre parênteses é carga, não mana.
    ability_uses_remaining: dict[str, int] = field(default_factory=dict)
    owner: int = 0  # 0 ou 1

    def has_tag(self, tag: str) -> bool:
        # Silence remove as tags/texto existentes no momento em que é aplicado,
        # mas não impede encantamentos posteriores. Portanto `silenced` não pode
        # mascarar a lista inteira de tags: buffs/keywords concedidos depois do
        # silêncio precisam funcionar normalmente.
        if tag == "DORMANT":
            return "DORMANT" in self.tags
        # Dormente não existe de fato na mesa para fins de Provocar/Furtividade/etc.
        # Mantemos a tag DORMANT visível para a UI, mas desativamos as demais.
        if "DORMANT" in self.tags:
            return False
        return tag in self.tags

    def has_tribe(self, tribe: str) -> bool:
        """Considera tribos derivadas. Toda FRUTA é também COMIDA."""
        if tribe in self.tribes:
            return True
        # FRUTA implica COMIDA
        if tribe == "COMIDA" and "FRUTA" in self.tribes:
            return True
        return False

    def can_attack(self) -> bool:
        if self.has_tag("DORMANT"):
            return False
        if self.cant_attack or self.frozen or self.skip_next_attack or self.attack <= 0:
            return False
        # CANT_ATTACK: tag intrínseca permanente (Baiano). ATTACK_LOCKED é a
        # versão temporária aplicada por Blitz. CANT_ATTACK_ONLY_FRIENDLY:
        # aura situacional.
        if (self.has_tag("CANT_ATTACK")
                or self.has_tag("ATTACK_LOCKED")
                or self.has_tag("CANT_ATTACK_ONLY_FRIENDLY")):
            return False
        max_attacks = 2 if self.has_tag("WINDFURY") else 1
        if self.attacks_this_turn >= max_attacks:
            return False
        # Charge / Rush podem atacar no turno em que entram
        if self.summoning_sick:
            return self.has_tag("CHARGE") or self.has_tag("RUSH")
        return True

    def can_attack_hero(self) -> bool:
        """RUSH não pode atacar herói no turno que entra. CHARGE pode."""
        if not self.can_attack():
            return False
        if self.has_tag("CANT_ATTACK_HERO_THIS_TURN"):
            return False
        if self.summoning_sick and self.has_tag("RUSH") and not self.has_tag("CHARGE"):
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "card_id": self.card_id,
            "name": self.name,
            "attack": self.attack,
            "health": self.health,
            "max_health": self.max_health,
            "tags": list(self.tags),
            "tribes": list(self.tribes),
            "summoning_sick": self.summoning_sick,
            "attacks_this_turn": self.attacks_this_turn,
            "divine_shield": self.divine_shield,
            "frozen": self.frozen,
            "silenced": self.silenced,
            "immune": self.immune,
            "cant_attack": self.cant_attack,
            "skip_next_attack": self.skip_next_attack,
            "activated_abilities_this_turn": self.activated_abilities_this_turn,
            "ability_uses_remaining": dict(self.ability_uses_remaining),
            "owner": self.owner,
            # Calculados - cliente usa pra decidir quando mostrar borda verde "pode atacar"
            "can_attack": self.can_attack(),
            "can_attack_hero": self.can_attack_hero(),
            # Lista de keywords ativas (visíveis). Silence já remove as keywords
            # antigas de `tags`; keywords adicionadas depois devem aparecer.
            "keywords": [
                t for t in self.tags
                if t in ("TAUNT", "DIVINE_SHIELD", "STEALTH", "LIFESTEAL",
                         "POISONOUS", "WINDFURY", "CHARGE", "RUSH",
                         "DEATHRATTLE", "BATTLECRY", "RESISTANT",
                         "SPELL_TARGET_IMMUNITY", "ENEMY_SPELL_TARGET_IMMUNITY",
                         "FRIENDLY_SPELL_TARGET_ONLY")
            ],
        }


@dataclass
class CardInHand:
    """Carta na mão. Carrega card_id pra resolver o template."""
    instance_id: str
    card_id: str
    cost_override: Optional[int] = None  # se não None, usa este custo
    # Modificador acumulado de custo (REDUCE_COST: -X, INCREASE_COST: +X). 
    # Aplicado em cima do custo base ao calcular o custo efetivo.
    cost_modifier: int = 0
    # Modificadores de stats (BUFF_DRAWN_CARD, ADD_MODIFIED_COPY_TO_HAND, etc).
    # São aplicados quando a carta é jogada como lacaio.
    stat_modifier: dict = field(default_factory=dict)  # {"attack": +X, "health": +X}
    extra_tags: list[str] = field(default_factory=list)
    # ECHO: cartas que foram jogadas com ECHO retornam à mão como temporárias.
    # No fim do turno são descartadas.
    echo_temporary: bool = False
    # Cartas reveladas por efeitos públicos permanecem identificáveis enquanto
    # ficam na mão, permitindo que o oponente as veja no lugar do cardback.
    revealed: bool = False

    def effective_cost(self) -> int:
        """Custo final considerando override e modificador."""
        from .cards import get_card
        base = self.cost_override if self.cost_override is not None else (get_card(self.card_id) or {}).get("cost", 0)
        return max(0, base + self.cost_modifier)

    def to_dict(self, hidden: bool = False) -> dict:
        if hidden and not self.revealed:
            return {"instance_id": self.instance_id, "hidden": True}
        from .cards import get_card
        card = get_card(self.card_id) or {}
        base_attack = card.get("attack")
        base_health = card.get("health")
        effective_attack = None
        effective_health = None
        stats_modified = False
        if card.get("type") == "MINION":
            effective_attack = (base_attack if base_attack is not None else 0) + self.stat_modifier.get("attack", 0)
            effective_health = (base_health if base_health is not None else 1) + self.stat_modifier.get("health", 0)
            stats_modified = bool(self.stat_modifier)
        eff_cost = self.effective_cost()
        base_cost = self.cost_override if self.cost_override is not None else card.get("cost", 0)
        return {
            "instance_id": self.instance_id,
            "card_id": self.card_id,
            "cost_override": self.cost_override,
            "cost_modifier": self.cost_modifier,
            "effective_cost": eff_cost,
            "effective_attack": effective_attack,
            "effective_health": effective_health,
            "stats_modified": stats_modified,
            "cost_modified": eff_cost != base_cost,
            "echo_temporary": self.echo_temporary,
            "extra_tags": list(self.extra_tags or []),
            "revealed": self.revealed,
            "hidden": False,
        }


@dataclass
class PlayerState:
    player_id: int  # 0 ou 1
    name: str
    portrait_url: Optional[str] = None
    hero_health: int = STARTING_HEALTH
    hero_armor: int = 0
    hero_max_health: int = STARTING_HEALTH
    mana: int = 0
    max_mana: int = 0
    deck: list[str] = field(default_factory=list)  # lista de card_ids
    hand: list[CardInHand] = field(default_factory=list)
    board: list[Minion] = field(default_factory=list)
    fatigue_counter: int = 0
    hero_attack: int = 0
    hero_attacks_this_turn: int = 0
    hero_immune: bool = False
    # Bloqueia targeting de feitiços inimigos contra o herói. Usado por
    # efeitos temporários como Zé Droguinha.
    hero_spell_target_immune: bool = False
    hero_frozen: bool = False
    hero_freeze_pending: bool = False
    # Cartas jogadas neste turno. Usado por Combo e pela UI.
    cards_played_this_turn: int = 0

    def is_dead(self) -> bool:
        return self.hero_health <= 0

    def to_dict(self, hide_hand: bool = False) -> dict:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "portrait_url": self.portrait_url,
            "hero_health": self.hero_health,
            "hero_armor": self.hero_armor,
            "hero_max_health": self.hero_max_health,
            "mana": self.mana,
            "max_mana": self.max_mana,
            "hand": [c.to_dict(hidden=hide_hand) for c in self.hand],
            "hand_size": len(self.hand),
            "deck_size": len(self.deck),
            "board": [m.to_dict() for m in self.board],
            "hero_attack": self.hero_attack,
            "hero_immune": self.hero_immune,
            "hero_spell_target_immune": self.hero_spell_target_immune,
            "hero_frozen": self.hero_frozen,
            "hero_attacks_this_turn": self.hero_attacks_this_turn,
            "fatigue_counter": self.fatigue_counter,
            "cards_played_this_turn": self.cards_played_this_turn,
            # Flags úteis pra UI saber regras especiais ativas neste turno.
            # Tomo Amaldiçoado deixa esse flag True para que a mão libere o
            # clique em feitiços mesmo sem mana suficiente (o custo vira HP).
            "next_spell_costs_health": False,
        }


@dataclass
class GameState:
    """Estado completo do jogo."""
    game_id: str
    players: list[PlayerState]
    current_player: int = 0
    turn_number: int = 0
    rng: random.Random = field(default_factory=random.Random)
    # log de eventos (visível pra ambos)
    event_log: list[dict] = field(default_factory=list)
    winner: Optional[int] = None  # None=jogando, -1=empate, 0/1=vencedor
    phase: str = "WAITING"  # WAITING, MULLIGAN, PLAYING, ENDED
    # mulligan: cada jogador retorna cartas que não quer
    mulligan_done: list[bool] = field(default_factory=lambda: [False, False])
    # Modificadores diferidos: ativados quando algum evento futuro acontece.
    # Exemplo: REDUCE_COST com mode=NEXT_CARD_PLAYED_THIS_TURN. Cada modifier
    # tem campos {kind, owner, amount, valid_filter, expires_on, etc}.
    pending_modifiers: list[dict] = field(default_factory=list)
    # Cemitério: lacaios que morreram, em ordem cronológica. Usado por
    # RESURRECT_LAST_FRIENDLY_DEAD_MINION e similares.
    graveyard: list[dict] = field(default_factory=list)
    # Escolha pendente de jogador. Quando não for None, a engine bloqueia
    # novas ações normais até o dono responder via WebSocket/API. Usado para
    # decisões que não devem ser resolvidas por heurística no servidor.
    pending_choice: Optional[dict] = None
    # Em testes unitários a engine pode continuar usando fallbacks/heurísticas.
    # O servidor habilita escolhas manuais nas partidas reais.
    manual_choices: bool = False
    # Modo especial de desenvolvimento: habilita ferramentas explícitas de
    # debug no servidor/UI, como inserir cartas na mão durante a partida.
    dev_mode: bool = False
    # Cartas compradas pela última ação de compra. Usado por efeitos encadeados
    # como Investidor, Foco e Guilãozinho. Armazenamos instance_ids da mão.
    last_drawn_card_instance_ids: list[str] = field(default_factory=list)
    # Lacaios ressurrectíveis recentemente (por turno) - limpado entre turnos
    # se o usuário decidir.
    last_target_id: Optional[str] = None  # último alvo de uma ação (pra ADJACENT_TO_PREVIOUS_TARGET)

    def opponent_of(self, player_id: int) -> PlayerState:
        return self.players[1 - player_id]

    def player_at(self, player_id: int) -> PlayerState:
        return self.players[player_id]

    def all_minions(self) -> list[Minion]:
        return [m for p in self.players for m in p.board]

    def find_minion(self, instance_id: str) -> Optional[tuple[Minion, int]]:
        """Retorna (minion, player_id) ou None."""
        for p in self.players:
            for m in p.board:
                if m.instance_id == instance_id:
                    return (m, p.player_id)
        return None

    def _pending_choice_for_viewer(self, viewer_id: int) -> Optional[dict]:
        """Retorna a escolha pendente visível ao viewer.

        O dono da escolha recebe detalhes completos. O oponente recebe apenas
        um marcador de espera, para não vazar informação privada do deck/mão.
        """
        if not self.pending_choice:
            return None
        owner = self.pending_choice.get("owner")
        if owner == viewer_id:
            # Não exponha detalhes internos de continuação/resolução para o cliente.
            return {k: v for k, v in self.pending_choice.items() if k not in ("resume",)}
        return {
            "choice_id": self.pending_choice.get("choice_id"),
            "owner": owner,
            "kind": self.pending_choice.get("kind"),
            "waiting": True,
        }

    def _public_statuses(self) -> list[dict]:
        statuses: list[dict] = []
        for pm in self.pending_modifiers:
            if pm.get("kind") == "hero_sot_damage":
                statuses.append({
                    "kind": "hero_burning",
                    "player_id": pm.get("player_id"),
                    "amount": pm.get("amount", 0),
                    "timing": pm.get("timing"),
                })
            elif pm.get("kind") == "minion_sot_damage":
                statuses.append({
                    "kind": "minion_burning",
                    "minion_id": pm.get("minion_id"),
                    "amount": pm.get("amount", 0),
                    "timing": pm.get("timing"),
                })
        return statuses

    def to_dict(self, viewer_id: int) -> dict:
        """Serializa o estado pra um jogador específico, escondendo info privada."""
        you = self.players[viewer_id].to_dict(hide_hand=False)
        opponent = self.players[1 - viewer_id].to_dict(hide_hand=True)
        # Anexa flags por-jogador derivados de pending_modifiers para a UI.
        def _visible_player_dict(pid: int):
            return you if pid == viewer_id else opponent

        def _minion_view(pid: int, minion_id: str):
            pdata = _visible_player_dict(pid)
            return next((m for m in pdata.get("board", []) if m.get("instance_id") == minion_id), None)

        for pm in self.pending_modifiers:
            if pm.get("consumed"):
                continue
            kind = pm.get("kind")
            owner = pm.get("owner")
            if kind == "next_spell_costs_health_instead_of_mana":
                if owner == viewer_id:
                    you["next_spell_costs_health"] = True
                else:
                    opponent["next_spell_costs_health"] = True
            elif kind == "buff_source_if_target_killed_by_opponent":
                found_src = self.find_minion(pm.get("source_minion_id"))
                found_target = self.find_minion(pm.get("target_id"))
                if found_src and found_target:
                    src_view = _minion_view(found_src[1], pm.get("source_minion_id"))
                    if src_view is not None:
                        target_minion = found_target[0]
                        src_view["linked_minion"] = {
                            "instance_id": target_minion.instance_id,
                            "card_id": target_minion.card_id,
                            "name": target_minion.name,
                        }

            elif kind == "cannot_attack_target":
                found_src = self.find_minion(pm.get("source_minion_id"))
                found_attacker = self.find_minion(pm.get("attacker_id"))
                if found_src and found_attacker:
                    src_view = _minion_view(found_src[1], pm.get("source_minion_id"))
                    if src_view is not None:
                        attacker = found_attacker[0]
                        src_view["linked_minion"] = {
                            "instance_id": attacker.instance_id,
                            "card_id": attacker.card_id,
                            "name": attacker.name,
                        }

            elif kind == "return_spell_to_deck_on_minion_death":
                found = self.find_minion(pm.get("minion_id"))
                if found:
                    minion_view = _minion_view(found[1], pm.get("minion_id"))
                    if minion_view is not None:
                        from .cards import get_card
                        card = get_card(pm.get("card_id") or "sub") or {}
                        minion_view["linked_card"] = {
                            "card_id": pm.get("card_id") or "sub",
                            "name": card.get("name", pm.get("card_id") or "sub"),
                        }

        # Previews dinâmicos de hover: Fusca Medicinal mostra qual lacaio o
        # próximo fim de turno ressuscitará, quando houver cemitério conhecido.
        last_dead_by_owner = {}
        for rec in self.graveyard:
            last_dead_by_owner[rec.get("owner")] = rec
        for pdata, pid in ((you, viewer_id), (opponent, 1 - viewer_id)):
            rec = last_dead_by_owner.get(pid)
            if not rec:
                continue
            for m in pdata.get("board", []):
                if m.get("card_id") == "fusca_medicinal":
                    m["related_card_ids"] = [rec.get("card_id")]
                    m["related_label"] = "Ressuscita"
                if m.get("card_id") == "frifas":
                    existing = list(m.get("related_card_ids") or [])
                    if "saudades" not in existing:
                        existing.append("saudades")
                    m["related_card_ids"] = existing
                    m["related_label"] = "Compra se possível"
        return {
            "game_id": self.game_id,
            "dev_mode": self.dev_mode,
            "current_player": self.current_player,
            "turn_number": self.turn_number,
            "winner": self.winner,
            "phase": self.phase,
            "viewer_id": viewer_id,
            "public_statuses": self._public_statuses(),
            "mulligan_done": list(self.mulligan_done),
            "pending_choice": self._pending_choice_for_viewer(viewer_id),
            "you": you,
            "opponent": opponent,
            "log": self.event_log[-50:],  # últimos 50 eventos
        }

    def log_event(self, event: dict):
        if not hasattr(self, "_event_seq"):
            self._event_seq = 0
        self._event_seq += 1
        event.setdefault("seq", self._event_seq)
        self.event_log.append(event)


def gen_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"
