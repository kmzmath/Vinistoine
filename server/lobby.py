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
from game.cards import all_cards, card_max_copies, is_collectible_card


VALID_GAME_MODES = {"constructed", "random"}


def normalize_game_mode(mode: str | None) -> str:
    mode = (mode or "constructed").strip().lower()
    if mode not in VALID_GAME_MODES:
        raise ValueError(f"Modo de jogo inválido: {mode}")
    return mode


def generate_random_deck(size: int = DECK_SIZE, rng: random.Random | None = None) -> list[str]:
    """Gera um deck aleatório de cartas colecionáveis.

    Respeita o limite padrão de cópias por carta enquanto houver pool suficiente.
    Tokens auxiliares, como a Moeda, não entram.
    """
    rng = rng or random.SystemRandom()
    eligible = [
        c["id"]
        for c in all_cards()
        if is_collectible_card(c.get("id"))
        and c.get("type") in {"MINION", "SPELL"}
    ]
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


@dataclass
class Match:
    match_id: str
    code: str  # código curto para amigos entrarem
    host_user_id: int
    host_nickname: str
    host_deck: list[str]
    game_mode: str = "constructed"
    state: Optional[GameState] = None
    guest_user_id: Optional[int] = None
    guest_nickname: Optional[str] = None
    guest_deck: Optional[list[str]] = None
    sockets: dict[int, WebSocket] = field(default_factory=dict)  # player_id -> ws
    user_to_player: dict[int, int] = field(default_factory=dict)  # user_id -> player_id (0/1)
    started: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LobbyManager:
    def __init__(self):
        self.matches: dict[str, Match] = {}  # match_id -> Match
        self.codes: dict[str, str] = {}      # code -> match_id

    def create_match(self, host_user_id: int, host_nickname: str,
                     host_deck: list[str], game_mode: str = "constructed") -> Match:
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
             guest_deck: list[str]) -> Optional[Match]:
        m = self.get_by_code(code)
        if m is None or m.guest_user_id is not None:
            return None
        # Um usuário não pode entrar na própria sala. Como sockets são
        # indexados por user_id, self-join sobrescreveria a conexão do host
        # e deixaria a sala em estado inconsistente.
        if guest_user_id == m.host_user_id:
            return None
        m.guest_user_id = guest_user_id
        m.guest_nickname = guest_nickname
        m.guest_deck = guest_deck
        return m

    def remove_match(self, match_id: str):
        m = self.matches.pop(match_id, None)
        if m:
            self.codes.pop(m.code, None)

    def list_open_matches(self) -> list[dict]:
        out = []
        for m in self.matches.values():
            if m.guest_user_id is None and not m.started:
                out.append({
                    "code": m.code,
                    "host": m.host_nickname,
                    "mode": m.game_mode,
                    "mode_label": "Decks aleatórios" if m.game_mode == "random" else "Deck construído",
                })
        return out


lobby = LobbyManager()


async def broadcast_state(match: Match):
    """Envia o estado serializado pra cada jogador."""
    if match.state is None:
        return
    from game.engine import compute_dynamic_cost
    from game.cards import get_card
    state = match.state
    for user_id, ws in list(match.sockets.items()):
        pid = match.user_to_player.get(user_id)
        if pid is None:
            continue
        snapshot = state.to_dict(viewer_id=pid)
        # Recalcula custo dinâmico (IN_HAND cost reductions) para o viewer
        viewer = state.players[pid]
        for hand_dict, hand_obj in zip(snapshot["you"]["hand"], viewer.hand):
            if hand_dict.get("hidden"):
                continue
            card = get_card(hand_obj.card_id)
            if card:
                effective = compute_dynamic_cost(state, viewer, hand_obj, card)
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


async def start_match_if_ready(match: Match):
    """Quando os 2 sockets estão conectados, iniciamos o jogo."""
    if match.started:
        return
    if len(match.sockets) < 2:
        return

    if match.game_mode == "random":
        host_deck = generate_random_deck()
        guest_deck = generate_random_deck()
    else:
        if match.guest_deck is None:
            return
        host_deck = match.host_deck
        guest_deck = match.guest_deck

    match.state = engine.new_game(
        match.host_nickname, host_deck,
        match.guest_nickname, guest_deck,
        manual_choices=True,
    )
    match.started = True
    await broadcast_state(match)
