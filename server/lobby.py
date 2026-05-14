"""
Gerenciador em memória de partidas e salas. Cada partida tem 2 conexões WebSocket.
"""
from __future__ import annotations
import asyncio
import json
import secrets
import random
from dataclasses import dataclass, field
from typing import Optional
from fastapi import WebSocket
from game import engine
from game.state import GameState, DECK_SIZE
from game.cards import all_cards, is_collectible_card, get_card


VALID_GAME_MODES = {"constructed", "random", "dev", "arena"}
DECKLESS_GAME_MODES = {"random", "dev", "arena"}
GAME_MODE_LABELS = {
    "constructed": "Deck construído",
    "random": "Decks aleatórios",
    "dev": "Modo dev",
    "arena": "Arena",
}

ARENA_CHOICES_TOTAL = 15
ARENA_OPTIONS_PER_PICK = 3
ARENA_COPIES_PER_PICK = 2
ARENA_COST_BUCKETS = ["0", "1", "2", "3", "4", "5", "6", "7+"]


def normalize_game_mode(mode: str | None) -> str:
    mode = (mode or "constructed").strip().lower()
    if mode not in VALID_GAME_MODES:
        raise ValueError(f"Modo de jogo inválido: {mode}")
    return mode


def _eligible_card_ids() -> list[str]:
    """Cartas que podem entrar em decks gerados pelo servidor."""
    return [
        c["id"]
        for c in all_cards()
        if is_collectible_card(c.get("id"))
        and c.get("type") in {"MINION", "SPELL"}
    ]


def generate_random_deck(size: int = DECK_SIZE, rng: random.Random | None = None) -> list[str]:
    """Gera um deck aleatório de cartas colecionáveis.

    Respeita o limite padrão de cópias por carta enquanto houver pool suficiente.
    Tokens auxiliares, como a Moeda, não entram.
    """
    rng = rng or random.SystemRandom()
    eligible = _eligible_card_ids()
    if not eligible:
        raise RuntimeError("Nenhuma carta colecionável disponível para gerar deck aleatório")

    # Modo aleatório usa singleton: no máximo 1 cópia de cada carta.
    unique_pool = list(dict.fromkeys(eligible))
    rng.shuffle(unique_pool)
    if len(unique_pool) >= size:
        return unique_pool[:size]

    # Fallback defensivo caso o JSON tenha menos cartas colecionáveis que o tamanho do deck.
    # Só neste caso inevitável repetimos.
    deck = list(unique_pool)
    while len(deck) < size:
        deck.append(rng.choice(unique_pool))
    return deck


def _arena_cost_bucket(card_id: str) -> str:
    card = get_card(card_id) or {}
    try:
        cost = int(card.get("cost", 0) or 0)
    except Exception:
        cost = 0
    return "7+" if cost >= 7 else str(max(0, cost))


def _arena_cost_curve(selected: list[str]) -> dict[str, int]:
    curve = {bucket: 0 for bucket in ARENA_COST_BUCKETS}
    for cid in selected:
        curve[_arena_cost_bucket(cid)] += ARENA_COPIES_PER_PICK
    return curve


def _arena_deck_from_selected(selected: list[str]) -> list[str]:
    deck: list[str] = []
    for cid in selected:
        deck.extend([cid] * ARENA_COPIES_PER_PICK)
    return deck


def _deal_arena_options(draft: dict):
    if len(draft["selected"]) >= ARENA_CHOICES_TOTAL:
        draft["options"] = []
        return
    offered = set(draft.get("offered") or [])
    available = [cid for cid in draft["pool"] if cid not in offered]
    if len(available) < ARENA_OPTIONS_PER_PICK:
        raise RuntimeError("Pool de cartas insuficiente para continuar a Arena sem repetir ofertas")
    draft["rng"].shuffle(available)
    options = available[:ARENA_OPTIONS_PER_PICK]
    draft["offered"].extend(options)
    draft["options"] = options


def _new_arena_draft(player_id: int) -> dict:
    pool = list(dict.fromkeys(_eligible_card_ids()))
    required_unique = ARENA_CHOICES_TOTAL * ARENA_OPTIONS_PER_PICK
    if len(pool) < required_unique:
        raise RuntimeError(
            f"Pool de cartas insuficiente para Arena: {len(pool)}/{required_unique} cartas únicas"
        )
    rng = random.SystemRandom()
    rng.shuffle(pool)
    draft = {
        "player_id": player_id,
        "pool": pool,
        "rng": rng,
        "selected": [],
        "offered": [],
        "options": [],
    }
    _deal_arena_options(draft)
    return draft


def _arena_snapshot(match: "Match", player_id: int) -> dict:
    draft = match.arena_drafts.get(player_id)
    if draft is None:
        return {"mode": "arena", "waiting": True}
    selected = list(draft.get("selected") or [])
    options = list(draft.get("options") or [])
    opponent = match.arena_drafts.get(1 - player_id) or {}
    choices_made = len(selected)
    return {
        "mode": "arena",
        "player_id": player_id,
        "round": min(choices_made + 1, ARENA_CHOICES_TOTAL),
        "total": ARENA_CHOICES_TOTAL,
        "choices_made": choices_made,
        "options_per_pick": ARENA_OPTIONS_PER_PICK,
        "copies_per_choice": ARENA_COPIES_PER_PICK,
        "options": [{"card_id": cid} for cid in options],
        "selected": [{"card_id": cid} for cid in selected],
        "cost_curve": _arena_cost_curve(selected),
        "done": choices_made >= ARENA_CHOICES_TOTAL,
        "opponent_done": len(opponent.get("selected") or []) >= ARENA_CHOICES_TOTAL,
    }


def init_arena_drafts(match: "Match"):
    if match.arena_draft_started:
        return
    match.arena_drafts = {
        0: _new_arena_draft(0),
        1: _new_arena_draft(1),
    }
    match.arena_draft_started = True


async def broadcast_arena_draft(match: "Match"):
    for user_id, ws in list(match.sockets.items()):
        pid = match.user_to_player.get(user_id)
        if pid is None:
            continue
        try:
            await ws.send_text(json.dumps({"type": "arena_draft", "draft": _arena_snapshot(match, pid)}))
        except Exception:
            pass


@dataclass
class Match:
    match_id: str
    code: str  # código curto para amigos entrarem
    host_user_id: int
    host_nickname: str
    host_deck: list[str]
    game_mode: str = "constructed"
    host_portrait: Optional[str] = None
    state: Optional[GameState] = None
    guest_user_id: Optional[int] = None
    guest_nickname: Optional[str] = None
    guest_deck: Optional[list[str]] = None
    guest_portrait: Optional[str] = None
    sockets: dict[int, WebSocket] = field(default_factory=dict)  # player_id -> ws
    user_to_player: dict[int, int] = field(default_factory=dict)  # user_id -> player_id (0/1)
    started: bool = False
    arena_draft_started: bool = False
    arena_drafts: dict[int, dict] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LobbyManager:
    def __init__(self):
        self.matches: dict[str, Match] = {}  # match_id -> Match
        self.codes: dict[str, str] = {}      # code -> match_id

    def create_match(self, host_user_id: int, host_nickname: str,
                     host_deck: list[str], game_mode: str = "constructed",
                     host_portrait: str | None = None) -> Match:
        match_id = secrets.token_hex(8)
        code = secrets.token_hex(3).upper()
        mode = normalize_game_mode(game_mode)
        match = Match(
            match_id=match_id,
            code=code,
            host_user_id=host_user_id,
            host_nickname=host_nickname,
            host_deck=host_deck,
            game_mode=mode,
            host_portrait=host_portrait,
        )
        self.matches[match_id] = match
        self.codes[code] = match_id
        return match

    def get_by_code(self, code: str) -> Optional[Match]:
        mid = self.codes.get(code.upper())
        if not mid:
            return None
        return self.matches.get(mid)

    def get(self, match_id: str) -> Optional[Match]:
        return self.matches.get(match_id)

    def join(self, code: str, guest_user_id: int, guest_nickname: str,
             guest_deck: list[str], guest_portrait: str | None = None) -> Optional[Match]:
        m = self.get_by_code(code)
        if m is None or m.guest_user_id is not None:
            return None
        if m.game_mode == "dev":
            return None
        # Um usuário não pode entrar na própria sala. Como sockets são
        # indexados por user_id, self-join sobrescreveria a conexão do host
        # e deixaria a sala em estado inconsistente.
        if guest_user_id == m.host_user_id:
            return None
        m.guest_user_id = guest_user_id
        m.guest_nickname = guest_nickname
        m.guest_deck = guest_deck
        m.guest_portrait = guest_portrait
        return m

    def remove_match(self, match_id: str):
        m = self.matches.pop(match_id, None)
        if m:
            self.codes.pop(m.code, None)

    def cancel_match(self, match_id: str, user_id: int) -> bool:
        """Cancela uma sala ainda não iniciada criada pelo próprio usuário."""
        m = self.matches.get(match_id)
        if m is None or m.started or m.host_user_id != user_id:
            return False
        self.remove_match(match_id)
        return True

    def list_open_matches(self) -> list[dict]:
        out = []
        for m in self.matches.values():
            if m.game_mode == "dev":
                continue
            if m.guest_user_id is None and not m.started:
                out.append({
                    "code": m.code,
                    "host": m.host_nickname,
                    "mode": m.game_mode,
                    "mode_label": GAME_MODE_LABELS.get(m.game_mode, "Deck construído"),
                })
        return out


lobby = LobbyManager()


async def broadcast_state(match: Match):
    """Envia o estado serializado pra cada jogador."""
    if match.state is None:
        return
    from game.engine import compute_displayed_cost
    from game.cards import get_card
    state = match.state
    for user_id, ws in list(match.sockets.items()):
        pid = match.user_to_player.get(user_id)
        if pid is None:
            continue
        snapshot = state.to_dict(viewer_id=pid)
        # Recalcula custo dinâmico (IN_HAND cost reductions + pending
        # next_card_cost_reduction) para o viewer. compute_displayed_cost
        # cobre Spiid 3 Anos / outras pendências que reduzem o próximo CARD.
        viewer = state.players[pid]
        for hand_dict, hand_obj in zip(snapshot["you"]["hand"], viewer.hand):
            if hand_dict.get("hidden"):
                continue
            card = get_card(hand_obj.card_id)
            if card:
                effective = compute_displayed_cost(state, viewer, hand_obj, card)
                hand_dict["effective_cost"] = effective
                base = hand_obj.cost_override if hand_obj.cost_override is not None else card.get("cost", 0)
                hand_dict["cost_modified"] = effective != base
        payload = {"type": "state", "state": snapshot}
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            pass


async def send_error(ws: WebSocket, message: str):
    try:
        await ws.send_text(json.dumps({"type": "error", "message": message}))
    except Exception:
        pass


def _arena_ready_to_start(match: Match) -> bool:
    if not match.arena_draft_started:
        return False
    return all(
        len((match.arena_drafts.get(pid) or {}).get("selected") or []) >= ARENA_CHOICES_TOTAL
        for pid in (0, 1)
    )


async def start_match_if_ready(match: Match):
    """Quando os sockets necessários estão conectados, iniciamos o jogo."""
    if match.started:
        return
    required_sockets = 1 if match.game_mode == "dev" else 2
    if len(match.sockets) < required_sockets:
        return

    if match.game_mode == "arena":
        if not match.guest_nickname:
            return
        if not match.arena_draft_started:
            init_arena_drafts(match)
            await broadcast_arena_draft(match)
            return
        if not _arena_ready_to_start(match):
            await broadcast_arena_draft(match)
            return
        host_deck = _arena_deck_from_selected(match.arena_drafts[0]["selected"])
        guest_deck = _arena_deck_from_selected(match.arena_drafts[1]["selected"])
    elif match.game_mode in DECKLESS_GAME_MODES:
        host_deck = generate_random_deck()
        guest_deck = generate_random_deck()
        if match.game_mode == "dev":
            match.guest_nickname = match.guest_nickname or "Oponente Dev"
    else:
        if match.guest_deck is None:
            return
        host_deck = match.host_deck
        guest_deck = match.guest_deck

    match.state = engine.new_game(
        match.host_nickname, host_deck,
        match.guest_nickname, guest_deck,
        seed=None,
        manual_choices=True,
        dev_mode=match.game_mode == "dev",
        player_a_portrait=match.host_portrait,
        player_b_portrait=match.guest_portrait,
    )
    if match.game_mode == "dev":
        # O modo dev deve cair direto na mesa para acelerar testes de cartas.
        engine.confirm_mulligan(match.state, 0, [])
        engine.confirm_mulligan(match.state, 1, [])
    match.started = True
    await broadcast_state(match)


async def choose_arena_card(match: Match, player_id: int, card_id: str | None) -> tuple[bool, str]:
    if match.game_mode != "arena":
        return False, "Esta partida não está no modo Arena"
    if match.started:
        return False, "O draft da Arena já terminou"
    if not match.arena_draft_started:
        init_arena_drafts(match)
    draft = match.arena_drafts.get(player_id)
    if draft is None:
        return False, "Draft da Arena indisponível"
    if len(draft["selected"]) >= ARENA_CHOICES_TOTAL:
        await broadcast_arena_draft(match)
        return False, "Você já terminou suas escolhas"
    if card_id not in draft.get("options", []):
        return False, "Carta inválida para esta escolha"

    draft["selected"].append(card_id)
    if len(draft["selected"]) < ARENA_CHOICES_TOTAL:
        _deal_arena_options(draft)

    if _arena_ready_to_start(match):
        await start_match_if_ready(match)
    else:
        await broadcast_arena_draft(match)
    return True, "OK"
