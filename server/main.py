"""
FastAPI app principal. Tudo em um único processo:
- API REST para auth, decks, lobby
- WebSocket para a partida
- Servir arquivos estáticos do cliente
"""
from __future__ import annotations
import json
import os
import secrets
import hashlib
from collections import Counter
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, conlist, constr
from typing import Optional

from .db import init_db, get_session, User, Deck
from .lobby import (
    lobby, broadcast_state, send_error, start_match_if_ready, Match,
    normalize_game_mode, DECKLESS_GAME_MODES, choose_arena_card,
)
from game import engine
from game.cards import all_cards, get_card, card_max_copies
from game.state import DECK_SIZE


SECRET = os.environ.get("SESSION_SECRET", secrets.token_hex(32))
ROOT_DIR = Path(__file__).parent.parent
STATIC_DIR = ROOT_DIR / "static"

NON_COLLECTIBLE_CARD_IDS = {"coin", "moeda", "moeda_encontrada"}
ALLOWED_CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "ALLOWED_CORS_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000",
    ).split(",")
    if origin.strip()
]

app = FastAPI(title="Card Game")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

SESSIONS: dict[str, int] = {}


def hash_password(pw: str) -> str:
    salt = "cardgame_static_salt_v1"
    return hashlib.sha256((salt + pw).encode()).hexdigest()


def get_user_from_token(token: Optional[str]) -> Optional[User]:
    if not token:
        return None
    user_id = SESSIONS.get(token)
    if user_id is None:
        return None
    with get_session() as s:
        return s.get(User, user_id)


def require_user(request: Request) -> User:
    token = request.headers.get("X-Auth-Token") or request.cookies.get("auth_token")
    user = get_user_from_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return user


_NICK = constr(min_length=1, max_length=40, strip_whitespace=True)
_PASS = constr(min_length=1, max_length=128)
_CARD_ID = constr(min_length=1, max_length=64)


class RegisterIn(BaseModel):
    nickname: _NICK
    password: _PASS


class LoginIn(BaseModel):
    nickname: _NICK
    password: _PASS


class DeckIn(BaseModel):
    name: constr(min_length=1, max_length=60, strip_whitespace=True)
    cards: conlist(_CARD_ID, max_length=DECK_SIZE)


class CreateMatchIn(BaseModel):
    deck_id: Optional[int] = None
    mode: constr(min_length=1, max_length=32) = "constructed"


class JoinMatchIn(BaseModel):
    code: constr(min_length=1, max_length=16, strip_whitespace=True)
    deck_id: Optional[int] = None


class PortraitIn(BaseModel):
    portrait: constr(min_length=1, max_length=120, strip_whitespace=True)


@app.on_event("startup")
def on_startup():
    init_db()
    from game.cards import load_cards
    load_cards()


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


_SHELL_HEADERS = {"Cache-Control": "no-cache, must-revalidate"}


def _shell_response() -> HTMLResponse:
    """Serve a shell com o modo Arena integrado sem duplicar o HTML estático."""
    html = (STATIC_DIR / "shell.html").read_text(encoding="utf-8")
    if '<option value="arena">Arena</option>' not in html:
        html = html.replace(
            '<option value="dev">Modo dev</option>',
            '<option value="dev">Modo dev</option>\n              <option value="arena">Arena</option>',
        )
    html = html.replace(
        'if (mode === "random" || mode === "dev") {',
        'if (mode === "random" || mode === "dev" || mode === "arena") {',
    )
    html = html.replace(
        ': "Modo <b>Decks aleatórios</b>: cada jogador recebe um deck aleatório de 30 cartas quando a partida começar.";',
        ': (mode === "arena" ? "Modo <b>Arena</b>: cada jogador escolhe 30 cartas individualmente antes da partida." : "Modo <b>Decks aleatórios</b>: cada jogador recebe um deck aleatório de 30 cartas quando a partida começar.");',
    )
    return HTMLResponse(html, headers=_SHELL_HEADERS)


@app.get("/lobby")
def lobby_page():
    return _shell_response()


@app.get("/deckbuilder")
def deckbuilder_page():
    return _shell_response()


@app.get("/portraits")
def portraits_page():
    return _shell_response()


@app.get("/play")
def play_page(request: Request):
    match_id = request.query_params.get("match")
    match = lobby.get(match_id) if match_id else None
    if match and match.game_mode == "arena" and not match.started:
        return FileResponse(STATIC_DIR / "arena.html")
    return FileResponse(STATIC_DIR / "game.html")


@app.get("/arena")
def arena_page():
    return FileResponse(STATIC_DIR / "arena.html", headers=_SHELL_HEADERS)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.post("/api/register")
def register(payload: RegisterIn):
    nick = payload.nickname.strip()
    if len(nick) < 2 or len(nick) > 30:
        raise HTTPException(400, "Nickname deve ter entre 2 e 30 caracteres")
    if len(payload.password) < 4:
        raise HTTPException(400, "Senha deve ter pelo menos 4 caracteres")
    with get_session() as s:
        existing = s.query(User).filter(User.nickname == nick).first()
        if existing:
            raise HTTPException(400, "Nickname já está em uso")
        u = User(nickname=nick, password_hash=hash_password(payload.password))
        s.add(u)
        s.commit()
        s.refresh(u)
        token = secrets.token_hex(24)
        SESSIONS[token] = u.id
        return {"token": token, "user_id": u.id, "nickname": u.nickname}


@app.post("/api/login")
def login(payload: LoginIn):
    with get_session() as s:
        u = s.query(User).filter(User.nickname == payload.nickname.strip()).first()
        if u is None or u.password_hash != hash_password(payload.password):
            raise HTTPException(401, "Credenciais inválidas")
        token = secrets.token_hex(24)
        SESSIONS[token] = u.id
        return {"token": token, "user_id": u.id, "nickname": u.nickname}


@app.get("/api/me")
def me(request: Request):
    user = require_user(request)
    return {"user_id": user.id, "nickname": user.nickname, "selected_portrait": user.selected_portrait}


@app.get("/api/cards")
def list_cards(include_tokens: bool = False):
    if include_tokens:
        return all_cards()
    return [c for c in all_cards() if c.get("id") not in NON_COLLECTIBLE_CARD_IDS]


@app.get("/api/card-images")
def list_card_images():
    images_dir = STATIC_DIR / "cards"
    available: dict[str, str] = {}
    if images_dir.exists():
        for f in images_dir.iterdir():
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                available[f.stem] = f"/static/cards/{f.name}"
                available.setdefault(f.stem.lower(), f"/static/cards/{f.name}")

    # A carta auxiliar Moeda tem id interno "coin", mas a imagem fica em
    # /static/moeda.png em vez de /static/cards/coin.png.
    moeda_path = STATIC_DIR / "moeda.png"
    if moeda_path.exists():
        moeda_url = "/static/moeda.png"
        available["coin"] = moeda_url
        available.setdefault("moeda", moeda_url)

    heroes_dir = STATIC_DIR / "heroes"
    hero_avatars: dict[str, str] = {}
    if heroes_dir.exists():
        for f in heroes_dir.iterdir():
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                hero_avatars[f.stem.lower()] = f"/static/heroes/{f.name}"
    return {"cards": available, "heroes": hero_avatars}


def available_portraits() -> dict[str, str]:
    portraits_dir = STATIC_DIR / "portraits"
    portraits: dict[str, str] = {}
    if portraits_dir.exists():
        for f in sorted(portraits_dir.iterdir(), key=lambda x: x.name.lower()):
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                portraits[f.name] = f"/static/portraits/{f.name}"
    return portraits


def selected_portrait_url(user: User) -> Optional[str]:
    if not user.selected_portrait:
        return None
    return available_portraits().get(user.selected_portrait)


@app.get("/api/portraits")
def list_portraits(request: Request):
    user = require_user(request)
    portraits = available_portraits()
    selected = user.selected_portrait if user.selected_portrait in portraits else None
    return {
        "portraits": [{"id": pid, "url": url, "name": Path(pid).stem.replace("_", " ").title()} for pid, url in portraits.items()],
        "selected": selected,
    }


@app.post("/api/portraits/select")
def select_portrait(payload: PortraitIn, request: Request):
    user = require_user(request)
    portraits = available_portraits()
    portrait = payload.portrait.strip()
    if portrait not in portraits:
        raise HTTPException(400, "Portrait inválida")
    with get_session() as s:
        u = s.get(User, user.id)
        if u is None:
            raise HTTPException(status_code=401, detail="Não autenticado")
        u.selected_portrait = portrait
        s.commit()
    return {"selected": portrait, "url": portraits[portrait]}


@app.get("/api/music")
def list_music():
    audio_dir = STATIC_DIR / "audio"
    tracks: list[str] = []
    if audio_dir.exists():
        for f in sorted(audio_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in (".m4a", ".mp4", ".mp3", ".ogg", ".webm", ".wav"):
                tracks.append(f"/static/audio/{f.name}")
    return {"tracks": tracks}


def validate_deck(card_ids: list[str]) -> Optional[str]:
    if len(card_ids) != DECK_SIZE:
        return f"O deck deve ter exatamente {DECK_SIZE} cartas (você tem {len(card_ids)})"
    counts = Counter(card_ids)
    for cid in counts:
        if cid in NON_COLLECTIBLE_CARD_IDS:
            return f"Carta não permitida no deck: {cid}"
        card = get_card(cid)
        if card is None:
            return f"Carta inválida: {cid}"
        if card.get("type") not in {"MINION", "SPELL"}:
            return f"Tipo de carta não permitido no deck: {card.get('name', cid)}"
    for cid, cnt in counts.items():
        max_c = card_max_copies(cid)
        if cnt > max_c:
            card = get_card(cid)
            return f"Máximo {max_c} cópias de '{card.get('name')}'"
    return None


@app.post("/api/decks")
def save_deck(payload: DeckIn, request: Request):
    user = require_user(request)
    err = validate_deck(payload.cards)
    if err:
        raise HTTPException(400, err)
    with get_session() as s:
        d = Deck(user_id=user.id, name=payload.name.strip()[:60] or "Deck", cards_json=json.dumps(payload.cards))
        s.add(d)
        s.commit()
        s.refresh(d)
        return {"id": d.id, "name": d.name, "cards": payload.cards}


@app.put("/api/decks/{deck_id}")
def update_deck(deck_id: int, payload: DeckIn, request: Request):
    user = require_user(request)
    err = validate_deck(payload.cards)
    if err:
        raise HTTPException(400, err)
    with get_session() as s:
        d = s.query(Deck).filter(Deck.id == deck_id, Deck.user_id == user.id).first()
        if d is None:
            raise HTTPException(404, "Deck não encontrado")
        d.name = payload.name.strip()[:60] or "Deck"
        d.cards_json = json.dumps(payload.cards)
        s.commit()
        s.refresh(d)
        return {"id": d.id, "name": d.name, "cards": d.card_ids()}


@app.get("/api/decks")
def list_decks(request: Request):
    user = require_user(request)
    with get_session() as s:
        decks = s.query(Deck).filter(Deck.user_id == user.id).order_by(Deck.created_at.desc()).all()
        return [{"id": d.id, "name": d.name, "cards": d.card_ids()} for d in decks]


@app.delete("/api/decks/{deck_id}")
def delete_deck(deck_id: int, request: Request):
    user = require_user(request)
    with get_session() as s:
        d = s.query(Deck).filter(Deck.id == deck_id, Deck.user_id == user.id).first()
        if d is None:
            raise HTTPException(404, "Deck não encontrado")
        s.delete(d)
        s.commit()
        return {"ok": True}


def get_deck_for_user(user_id: int, deck_id: int) -> Optional[list[str]]:
    with get_session() as s:
        d = s.query(Deck).filter(Deck.id == deck_id, Deck.user_id == user_id).first()
        if d is None:
            return None
        return d.card_ids()


@app.post("/api/match/create")
def create_match(payload: CreateMatchIn, request: Request):
    user = require_user(request)
    try:
        mode = normalize_game_mode(payload.mode)
    except ValueError:
        raise HTTPException(400, "Modo de jogo inválido")

    if mode in DECKLESS_GAME_MODES:
        deck: list[str] = []
    else:
        if payload.deck_id is None:
            raise HTTPException(400, "Deck obrigatório para modo construído")
        deck = get_deck_for_user(user.id, payload.deck_id)
        if deck is None:
            raise HTTPException(404, "Deck não encontrado")
        err = validate_deck(deck)
        if err:
            raise HTTPException(400, f"Deck inválido: {err}")

    m = lobby.create_match(user.id, user.nickname, deck, game_mode=mode, host_portrait=selected_portrait_url(user))
    return {"match_id": m.match_id, "code": m.code, "mode": m.game_mode}


@app.post("/api/match/join")
def join_match(payload: JoinMatchIn, request: Request):
    user = require_user(request)
    code = payload.code.strip()
    room = lobby.get_by_code(code)
    if room is None:
        raise HTTPException(400, "Sala não encontrada ou já cheia")

    if room.game_mode == "dev":
        raise HTTPException(400, "Modo dev é singleplayer")

    if room.game_mode in DECKLESS_GAME_MODES:
        deck: list[str] = []
    else:
        if payload.deck_id is None:
            raise HTTPException(400, "Deck obrigatório para entrar em sala de deck construído")
        deck = get_deck_for_user(user.id, payload.deck_id)
        if deck is None:
            raise HTTPException(404, "Deck não encontrado")
        err = validate_deck(deck)
        if err:
            raise HTTPException(400, f"Deck inválido: {err}")

    m = lobby.join(code, user.id, user.nickname, deck, guest_portrait=selected_portrait_url(user))
    if m is None:
        raise HTTPException(400, "Sala não encontrada ou já cheia")
    return {"match_id": m.match_id, "code": m.code, "mode": m.game_mode}


@app.get("/api/match/list")
def list_matches():
    return lobby.list_open_matches()


@app.post("/api/match/{match_id}/cancel")
def cancel_match(match_id: str, request: Request):
    user = require_user(request)
    if not lobby.cancel_match(match_id, user.id):
        raise HTTPException(400, "Sala não encontrada, já iniciada ou criada por outro usuário")
    return {"ok": True}


def _is_origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    host = websocket.headers.get("host")
    if host:
        try:
            from urllib.parse import urlparse
            origin_host = urlparse(origin).netloc
            if origin_host and origin_host == host:
                return True
        except Exception:
            pass
    return origin in ALLOWED_CORS_ORIGINS


@app.websocket("/ws/match/{match_id}")
async def match_ws(websocket: WebSocket, match_id: str, token: str):
    if not _is_origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    user = get_user_from_token(token)
    if user is None:
        await websocket.send_text(json.dumps({"type": "error", "message": "Token inválido"}))
        await websocket.close()
        return
    match = lobby.get(match_id)
    if match is None:
        await send_error(websocket, "Partida não encontrada")
        await websocket.close()
        return

    if user.id == match.host_user_id:
        player_id = 0
    elif user.id == match.guest_user_id:
        player_id = 1
    else:
        await send_error(websocket, "Você não está nesta partida")
        await websocket.close()
        return

    match.sockets[user.id] = websocket
    match.user_to_player[user.id] = player_id

    await websocket.send_text(json.dumps({
        "type": "joined",
        "your_player_id": player_id,
        "host_nickname": match.host_nickname,
        "guest_nickname": match.guest_nickname,
        "code": match.code,
        "mode": match.game_mode,
    }))

    await start_match_if_ready(match)
    if match.started and match.state is not None:
        # Reconnects e redirecionamentos pós-Arena precisam receber o estado
        # atual imediatamente. Sem isso, o cliente ficava na tela de espera.
        await broadcast_state(match)

    try:
        while True:
            msg = await websocket.receive_text()
            try:
                data = json.loads(msg)
            except Exception:
                await send_error(websocket, "JSON inválido")
                continue
            await handle_action(match, user.id, player_id, data)
    except WebSocketDisconnect:
        match.sockets.pop(user.id, None)
        for uid, ws in match.sockets.items():
            try:
                await ws.send_text(json.dumps({"type": "opponent_disconnected"}))
            except Exception:
                pass
        if not match.sockets and not match.started:
            lobby.remove_match(match.match_id)


async def handle_action(match: Match, user_id: int, player_id: int, data: dict):
    action = data.get("action")

    if match.state is None:
        if action == "arena_pick":
            ok, msg = await choose_arena_card(match, player_id, data.get("card_id"))
            if not ok:
                await send_error(match.sockets[user_id], msg)
            return
        await send_error(match.sockets[user_id], "Jogo ainda não começou")
        return

    async with match.lock:
        if action == "mulligan":
            swap_ids = data.get("swap", [])
            if not isinstance(swap_ids, list):
                swap_ids = []
            engine.confirm_mulligan(match.state, player_id, swap_ids)
            await broadcast_state(match)
            return

        if action == "play_card":
            ok, msg = engine.play_card(
                match.state, player_id, data.get("hand_id"),
                chosen_target=data.get("target"), chosen_targets=data.get("targets"),
                board_position=data.get("position"), chose_index=data.get("chose_index"),
                empowered=bool(data.get("empowered", False)), direction=data.get("direction"),
            )
            if not ok:
                await send_error(match.sockets[user_id], msg)
            await broadcast_state(match)
            return

        if action == "attack":
            ok, msg = engine.attack(match.state, player_id, data.get("attacker_id"), data.get("target_id"))
            if not ok:
                await send_error(match.sockets[user_id], msg)
            await broadcast_state(match)
            return

        if action == "activate_ability":
            ok, msg = engine.activate_ability(
                match.state, player_id, data.get("minion_id"),
                ability_index=data.get("ability_index", 0), chosen_target=data.get("target"),
                chosen_targets=data.get("targets"), zone=data.get("zone"), position=data.get("position"),
            )
            if not ok:
                await send_error(match.sockets[user_id], msg)
            await broadcast_state(match)
            return

        if action == "choice_response":
            ok, msg = engine.resolve_choice(match.state, player_id, data.get("choice_id"), data.get("response") or {})
            if not ok:
                await send_error(match.sockets[user_id], msg)
            await broadcast_state(match)
            return

        dev_actions = {
            "dev_add_card": lambda: engine.dev_add_card_to_hand(match.state, player_id, data.get("card_id"), target_player_id=data.get("target_player_id")),
            "dev_draw_card": lambda: engine.dev_draw_card(match.state, player_id, data.get("card_id"), target_player_id=data.get("target_player_id")),
            "dev_add_to_deck": lambda: engine.dev_add_card_to_deck(match.state, player_id, data.get("card_id"), target_player_id=data.get("target_player_id"), position=data.get("position", "TOP")),
            "dev_summon_minion": lambda: engine.dev_summon_minion(match.state, player_id, data.get("card_id"), target_player_id=data.get("target_player_id"), board_position=data.get("position")),
            "dev_set_mana": lambda: engine.dev_set_mana(match.state, player_id, data.get("mana", 10), data.get("max_mana", 10), target_player_id=data.get("target_player_id")),
            "dev_clear_hand": lambda: engine.dev_clear_hand(match.state, player_id, target_player_id=data.get("target_player_id")),
        }
        if action in dev_actions:
            ok, msg = dev_actions[action]()
            if not ok:
                await send_error(match.sockets[user_id], msg)
            await broadcast_state(match)
            return

        if action == "end_turn":
            turn_player = match.state.current_player if match.state.dev_mode else player_id
            ok = engine.end_turn(match.state, turn_player)
            if not ok:
                await send_error(match.sockets[user_id], "Não é seu turno ou há escolha pendente")
            await broadcast_state(match)
            return

        if action == "concede":
            match.state.phase = "ENDED"
            match.state.winner = 1 - player_id
            match.state.log_event({"type": "concede", "player": player_id})
            await broadcast_state(match)
            return

        await send_error(match.sockets[user_id], f"Ação desconhecida: {action}")


@app.get("/healthz")
def health():
    return {"ok": True}
