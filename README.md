# Card Game (Hearthstone-like custom)

Jogo de cartas digital 1v1 inspirado em Hearthstone, com 240 cartas customizadas,
pensado para hospedar no [Render](https://render.com) e jogar de forma privada
entre amigos.

Todos os jogadores têm acesso a todas as 240 cartas. Cada um monta seu próprio
deck de 30 cartas (até 2 cópias por carta), entra numa sala via código, e joga
uma partida 1v1 em tempo real via WebSocket.

## Stack

- **Backend:** Python 3.11 + FastAPI + WebSocket nativo
- **Banco:** SQLite por padrão; PostgreSQL opcional via `DATABASE_URL`
- **Frontend:** HTML + CSS + JS vanilla (sem framework, sem build)
- **Deploy:** um único Web Service no Render servindo API + WebSocket + estáticos

## Estrutura do projeto

```
cardgame/
├── game/                  # Engine pura, sem dependência de rede
│   ├── state.py           # Modelos: GameState, PlayerState, Minion, CardInHand
│   ├── cards.py           # Loader do JSON de cartas
│   ├── targeting.py       # Sistema de targeting (CHOSEN, RANDOM_*, ALL_*, etc)
│   ├── effects.py         # Resolver de efeitos com handlers registrados
│   ├── engine.py          # new_game, start_turn, end_turn, play_card, attack, cleanup
│   └── data/cards.json    # As 240 cartas + token "Moeda"
├── server/                # Camada web
│   ├── db.py              # SQLAlchemy: User, Deck
│   ├── lobby.py           # Lobby em memória, salas, broadcast de estado
│   └── main.py            # FastAPI: /api/* + /ws/match/{id}
├── static/                # Frontend
│   ├── index.html         # Login / Registro
│   ├── lobby.html         # Saguão
│   ├── deckbuilder.html   # Construtor de decks
│   ├── game.html          # Tela de partida
│   └── css/main.css
├── docs/
│   └── CARD_COVERAGE.md # Relatório gerado de cobertura cards.json × engine
├── scripts/
│   └── card_coverage.py # Gera docs/CARD_COVERAGE.md
├── tests/
│   ├── test_engine.py
│   ├── test_keywords.py
│   ├── test_lote1.py
│   ├── test_lote2.py
│   ├── test_lote3_familia1.py
│   ├── test_pending_choices.py
│   ├── test_server_rules.py
│   ├── test_targeting_filters.py
│   ├── test_card_schema_coverage.py
│   └── test_lote9_cost_mana_turn.py
├── requirements.txt
├── render.yaml
├── Procfile
├── runtime.txt
└── README.md
```

## Rodando localmente

Pré-requisitos: Python 3.11+ instalado.

```bash
# Clone o projeto e entre na pasta
cd cardgame

# Crie um virtualenv (recomendado)
python -m venv venv
source venv/bin/activate          # Linux/Mac
# venv\Scripts\activate           # Windows

# Instale as dependências
pip install -r requirements.txt

# Suba o servidor
uvicorn server.main:app --reload --host 127.0.0.1 --port 8000
```

Abra <http://127.0.0.1:8000> no navegador. Crie duas contas (em duas abas
anônimas/navegadores diferentes), monte um deck em cada, e crie/entre numa sala
no lobby usando o código gerado.

### Rodando os testes

```bash
pip install pytest
pytest -v
```

A suíte cobre engine, keywords, handlers por lote, regras do servidor, filtros de targeting, escolhas pendentes, múltiplos alvos e compatibilidade entre `cards.json` e a engine.

Para regenerar o relatório de cobertura das cartas:

```bash
python scripts/card_coverage.py
```

Isso atualiza `docs/CARD_COVERAGE.md` com actions, triggers, target modes e conditions usados pelo JSON.

## Deploy no Render

A forma mais simples (Blueprint via `render.yaml`):

1. Suba o projeto pra um repo no GitHub.
2. No Render, vá em **New → Blueprint** e aponte para o repo.
3. O Render lê o `render.yaml` e cria um Web Service Python free com:
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn server.main:app --host 0.0.0.0 --port $PORT`
   - Health check em `/healthz`
   - `SESSION_SECRET` gerado automaticamente
4. Aguarde o build (~3 min). O serviço fica disponível em
   `https://cardgame-XXXX.onrender.com`.

### Forma manual (sem Blueprint)

1. **New → Web Service** no Render, conecte ao repo.
2. **Runtime:** Python 3
3. **Build Command:** `pip install -r requirements.txt`
4. **Start Command:** `uvicorn server.main:app --host 0.0.0.0 --port $PORT`
5. **Health Check Path:** `/healthz`
6. (Opcional) Adicione a env var `SESSION_SECRET` com qualquer string longa
   aleatória.

### Persistência: SQLite vs Postgres

Por padrão o app usa SQLite num arquivo local — simples, mas o plano free do
Render limpa o disco a cada redeploy, então **as contas e decks somem**
quando você redeploya.

Pra persistir, use Postgres:

1. No Render, **New → PostgreSQL** (plano free dura 30 dias e depois precisa
   ser recriado, ou você upgrade pra pago).
2. No painel do Web Service, em **Environment**, adicione:
   - `DATABASE_URL` = a connection string interna do banco (`postgresql://...`)
3. Redeploy.

Ou descomente os blocos no `render.yaml` que já preparei pra isso.

### Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `PORT` | (Render injeta) | Porta do HTTP |
| `SESSION_SECRET` | gerado | Salt extra dos tokens de sessão |
| `DATABASE_URL` | `sqlite:///./cardgame.db` | Conexão SQLAlchemy |

## Imagens das cartas e dos heróis

As imagens das cartas vão em duas pastas:

- `static/cards/{card_id}.png` (ou `.jpg`, `.webp`) — imagem da carta. O
  `card_id` é o `id` da carta no `game/data/cards.json` (ex.: `vini_zumbi.png`).
- `static/heroes/{username}.png` — avatar do herói/jogador. O `username` é o
  nickname em minúsculas (ex.: `lucas.png` para o jogador "Lucas").

### Como a imagem é usada

A imagem de cada carta é tratada como **full-art estilo Hearthstone**: ela
preenche o retângulo inteiro da carta na mão, no construtor de decks e na
tela de mulligan. Custo, atk, hp, nome e texto da carta são lidos
diretamente da própria arte — o cliente não desenha overlays por cima
quando a imagem está presente.

No campo de batalha, o lacaio é mostrado num anel circular. O `background`
é zoomado e centrado na parte alta da carta (onde costuma estar a
ilustração principal), evitando pegar moldura/banner.

**Não é preciso ter as 240 imagens prontas** — adicione aos poucos. Quando
uma imagem está ausente, o cliente mostra automaticamente um fallback
textual (custo no canto, nome, texto, atk/hp) usando o tema escuro/dourado.
O servidor expõe `GET /api/card-images` que lista exatamente quais arquivos
existem, então não há requests 404 no console.

### Resolução recomendada

- **Cartas:** 400×543px ou maior, formato PNG/JPG/WebP. A proporção da carta
  na mão é aproximadamente 130:200 (≈0.65), então qualquer formato vertical
  do Hearthstone (~0.74) é cropado ligeiramente nas laterais — se quiser
  encaixe perfeito, use a proporção 0.65.
- **Heróis:** 512×512px ou maior, formato quadrado.

## Indicadores visuais de keyword

No campo, os lacaios mostram suas habilidades de forma visual, sem texto:

| Keyword | Indicador |
|---|---|
| **Provocar** | Anel prata grosso pontilhado girando ao redor |
| **Escudo Divino** | Brilho dourado pulsante no anel |
| **Furtividade** | Anel pontilhado com névoa cobrindo o lacaio |
| **Congelado** | Anel azul gélido |
| **Pode atacar** | Anel verde brilhante pulsante |
| **Último Suspiro** | Selo dourado com ✠ no canto |
| **Roubo de Vida** | Selo vermelho com ♥ |
| **Veneno** | Selo verde com ☠ |
| **Fúria dos Ventos** | Selo azul com ≫ |

Passar o mouse sobre o lacaio mostra o nome, o texto da carta e a lista de
keywords ativas em tooltip.

## Animações em combate

- **Atacando**: o lacaio "salta" em direção ao alvo e volta para a posição
- **Recebendo dano**: tremor com flash branco/vermelho + número flutuante (`-N`)
  em vermelho
- **Curando**: número flutuante (`+N`) em verde
- **Escudo Divino quebrado**: símbolo `✦` flutuando em dourado
- **Lacaio morrendo**: encolhe e desaparece com leve rotação
- **Lacaio entrando em campo**: cresce de zero com rotação de boas-vindas



### Regras implementadas

- 30 HP iniciais, mana progressiva (1 a 10), mão máxima de 10, campo máximo de 7
- Mulligan inicial (3 cartas pro primeiro jogador, 4 + Moeda pro segundo)
- Compra de uma carta no início de cada turno + dano de fadiga progressivo
- Lacaios com `attack`, `health`, `summoning sickness`
- Tags suportadas: `TAUNT`, `CHARGE`, `RUSH`, `DIVINE_SHIELD`, `LIFESTEAL`,
  `POISONOUS`, `STEALTH`, `WINDFURY`, `BATTLECRY`, `DEATHRATTLE`, `FREEZE`,
  `RESISTANT`, imunidade a alvo de feitiço e restrições temporárias de ataque/dano
- Trocas de dano simultâneas, mortes em cadeia via cleanup iterativo,
  triggers de `ON_DEATH` (deathrattle), `START_OF_TURN`, `END_OF_TURN`,
  `AFTER_FRIENDLY_MINION_PLAY`, etc.
- Vitória/derrota/empate quando vida do herói chega a 0

### Cobertura de ações

A cobertura agora é medida automaticamente por `game/card_coverage.py` e pelos
testes em `tests/test_card_schema_coverage.py`. No estado atual:

- Cartas carregadas: **241** (240 colecionáveis + token `coin`)
- Actions distintas usadas no JSON: **157**
- Handlers registrados na engine: **158**
- Actions usadas pelo JSON sem handler: **0**
- Ocorrências de action cobertas: **299/299** (**100,0%**)
- Target modes desconhecidos: **0**
- Condition types sem suporte explícito: **0**
- Triggers ainda não disparadas diretamente pela engine: **0**

A lista detalhada fica em `docs/CARD_COVERAGE.md`.

Ações com handler ausente **não quebram o jogo**: a carta entra normalmente em
campo (se for lacaio) ou é gasta a mana (se for feitiço), e o efeito é
registrado como `unimplemented_action` no log da partida.

O teste de cobertura não exige que tudo esteja pronto agora. Ele exige que tudo
que ainda falta esteja explicitamente rastreado. Se você adicionar uma carta com
action, trigger, target mode ou condition nova, o teste acusa até você implementar
ou marcar conscientemente como pendente.

### Adicionando novos handlers

Tudo ficou em `game/effects.py`. O padrão é simples:

```python
@handler("REDUCE_COST")
def _reduce_cost(state, eff, source_owner, source_minion, ctx):
    """Reduz o custo de mana de cartas na mão."""
    amount = eff.get("amount", 1)
    targets = targeting.resolve_targets(
        state, eff.get("target") or {},
        source_owner, source_minion, ctx.get("chosen_target")
    )
    for t in targets:
        if isinstance(t, CardInHand):
            t.cost_modifier -= amount
            state.log_event({
                "type": "cost_reduced",
                "card": t.instance_id,
                "amount": amount,
            })
```

Cada handler recebe:
- `state` — `GameState` da partida
- `eff` — o dict do efeito vindo do JSON da carta
- `source_owner` — id do jogador que disparou (0 ou 1)
- `source_minion` — `Minion` que disparou, ou `None` se foi feitiço/herói
- `ctx` — contexto da resolução (`chosen_target`, `is_spell`, etc)

Para priorizar próximos handlers, consulte `docs/CARD_COVERAGE.md` ou rode `python scripts/card_coverage.py`.

## Protocolo WebSocket

Cliente → servidor:
```json
{ "action": "play_card", "hand_id": "h_xxx", "target": "hero:1", "position": 2 }
{ "action": "attack",    "attacker_id": "m_xxx", "target_id": "m_yyy" }
{ "action": "end_turn" }
{ "action": "mulligan",  "swap": ["h_a", "h_b"] }
{ "action": "concede" }
```

Servidor → cliente:
```json
{ "type": "joined",  "you": 0, "opponent": "Bob" }
{ "type": "state",   "state": { ... estado completo censurado ... } }
{ "type": "error",   "msg": "..." }
{ "type": "opponent_disconnected" }
```

O estado mandado pra cada jogador esconde a mão e o deck do oponente
(só revela contagens), conforme a especificação de informação parcial.

## Limitações conhecidas

- **Auth simples:** SHA-256 com salt estático e tokens em memória. Adequado pra
  jogo entre amigos privado, **não pra produção pública**. Se você quiser abrir
  o serviço, troque por algo decente (Argon2 + JWT/cookies seguros).
- **Lobby em memória:** salas e estados de partida vivem só no processo. Se o
  Render reiniciar o serviço (ele faz isso ocasionalmente no plano free), todas
  as partidas em andamento são perdidas. Decks e contas persistem no banco.
- **Sleep do plano free:** o Render hiberna o serviço gratuito após 15 min sem
  tráfego. A primeira request depois acorda em ~30 s. Pra jogar, abra o link e
  espere a página carregar antes de chamar os amigos.
- **134 ações sem handler:** ver seção acima.
- **Sem armas, sem poder heroico:** o JSON de cartas que você mandou não inclui
  cartas de tipo `WEAPON` nem heróis customizáveis. Se quiser adicionar, é
  estender `state.py` (campo `weapon`) e `engine.py` (lógica de durabilidade).

## Licença

Projeto privado para uso entre amigos. As 240 cartas são de autoria do
proprietário do repositório.

## Atualizações aplicadas — Lote 1 e Lote 2

### Lote 1 — robustez básica

- O servidor bloqueia o host tentando entrar na própria sala.
- Tokens/cartas auxiliares como `coin` não são aceitos em decks.
- `/api/cards` retorna apenas cartas colecionáveis para o cliente.
- `end_turn` informa erro quando não é uma ação válida.
- O CORS foi ajustado para usar `ALLOWED_CORS_ORIGINS` em vez de liberar tudo por padrão.
- `targeting.py` agora valida filtros como `required_tribe`, `required_tag` e `SAME_AS_PREVIOUS_TARGET`.
- `play_card` valida alvo antes de gastar mana/remover carta da mão.
- Corrigido o caso de carta com `health: 0` ser transformada em `1` por uso indevido de `or 1`.
- Condições desconhecidas em `CONDITIONAL_EFFECTS` agora são registradas no log como `unimplemented_condition`.

### Lote 2 — escolhas pendentes e múltiplos alvos

Foi adicionada infraestrutura básica de escolha pendente no servidor e na engine.

Agora `GameState` possui:

```python
pending_choice: Optional[dict]
manual_choices: bool
```

Quando `manual_choices=True`, algumas cartas deixam de usar heurística automática e passam a pedir uma decisão real ao jogador. O servidor bloqueia ações normais enquanto uma escolha pendente existir.

Escolhas já suportadas:

- `reorder_top_cards`: reorganizar cartas reveladas do topo do próprio deck.
- `swap_revealed_top_cards`: escolher se troca ou mantém os topos revelados dos dois decks.

Novo payload WebSocket do cliente para o servidor:

```json
{
  "action": "choice_response",
  "choice_id": "choice_xxxxxxxx",
  "response": {
    "order": [2, 0, 1]
  }
}
```

Ou, para troca de topo:

```json
{
  "action": "choice_response",
  "choice_id": "choice_xxxxxxxx",
  "response": {
    "swap": true
  }
}
```

O protocolo de `play_card` também aceita múltiplos alvos:

```json
{
  "action": "play_card",
  "hand_id": "h_xxxxxxxx",
  "targets": ["m_primeiro", "m_segundo"],
  "position": 2,
  "chose_index": 0
}
```

O campo antigo `target` continua funcionando para cartas com um único alvo.

### Testes adicionados

Foram adicionados testes para:

- bloqueio de ações enquanto há escolha pendente;
- resolução de reorganização de topo do deck;
- resolução de troca de topos revelados;
- suporte de `play_card` para múltiplos alvos sequenciais.

Resultado esperado após instalar dependências:

```bash
pytest -q
```

Resultado local deste lote:

```text
259 passed, 1 skipped
```


### Lote 3 — cobertura automática do `cards.json`

Foi adicionado um analisador de schema/cobertura para impedir que o JSON das cartas evolua sem a engine acompanhar.

Novos arquivos:

- `game/card_coverage.py` — coleta actions, triggers, target modes e conditions usados pelas cartas.
- `scripts/card_coverage.py` — gera `docs/CARD_COVERAGE.md`.
- `docs/CARD_COVERAGE.md` — relatório legível da cobertura atual.
- `tests/test_card_schema_coverage.py` — teste de regressão que falha quando aparece vocabulário novo não rastreado.

Com isso, o projeto passa a ter um radar objetivo do que falta implementar nas cartas.

### Lote 4 — keywords e valores modificados na mão

Correções aplicadas a bugs reportados nos testes manuais:

- `ECHO` agora pode ser jogado repetidamente no mesmo turno enquanto houver mana. Cada uso gera uma nova cópia temporária, e todas as cópias temporárias somem no fim do turno.
- `RESISTANT` reduz 1 de dano de qualquer fonte antes de checar Escudo Divino. Se o dano final for 0, o Escudo Divino não é quebrado.
- Cartas full-art na mão agora mostram overlays de custo, ataque e vida atuais, incluindo cartas modificadas.
- Tags temporárias com `duration: "UNTIL_END_OF_TURN"` ou `"THIS_TURN"` são removidas no fim do turno do dono do efeito.
- `AFTER_YOU_PLAY_CARD` agora dispara tanto para lacaios quanto para feitiços.
- `WHILE_DAMAGED` foi tratado para casos como Edu Putasso, sem empilhar infinitamente.
- `WHILE_STEALTHED` agora mantém imunidade enquanto o lacaio estiver furtivo e remove a imunidade quando a furtividade acaba.
- Reduções `IN_HAND` com ação `REDUCE_COST`, como Pizza, agora são consideradas por `compute_dynamic_cost`.

Testes adicionados:

- `tests/test_lote4_keywords_and_hand.py`

Resultado local deste lote:

```text
259 passed, 1 skipped
```

### Lote 5 — descarte/compra/revelação e cartas recém-compradas

Correções aplicadas a cartas específicas do backlog manual:

- `DISCARD_CARD` em partidas com escolha manual agora abre uma escolha de descarte para o jogador, em vez de descartar aleatoriamente. Isso corrige Troca Justa e Mundo dos Negócios.
- A engine agora consegue pausar um efeito, receber a escolha do jogador e continuar os efeitos restantes da carta. Ex.: descarta uma carta e depois compra uma carta.
- `CHOOSE_ONE_DRAW_ONE_DISCARD_OTHER` foi implementado para SAS: mostra as cartas do topo, o jogador escolhe uma para comprar e as outras são descartadas.
- `DRAW_CARD` com `reveal: true` agora registra evento `reveal_drawn_card`, usado por cartas como Guilãozinho.
- `BUFF_DRAWN_CARD` agora aplica modificadores de ataque/vida à carta recém-comprada. Isso corrige Foco.
- `REDUCE_COST` com target `DRAWN_CARD` agora reduz o custo da carta recém-comprada. Isso corrige Guilãozinho.
- `OPPONENT_STEALS_RANDOM_DRAWN_CARD` agora rouba uma das cartas compradas pelo efeito anterior, sem comprar carta extra. Isso corrige Investidor.

Testes adicionados:

- `tests/test_lote5_draw_discard.py`

Resultado local deste lote:

```text
259 passed, 1 skipped
```



### Lote 6 — invocação, cópias, destruição e auras simples

Correções aplicadas a cartas específicas do backlog manual e a handlers de invocação:

- `SUMMON` agora aceita `card_id` direto, além de `card.id`. Isso corrige Pizzaiolo invocando Pizza ao morrer.
- `SUMMON_CARD` foi implementado como alias de invocação com suporte a tags concedidas.
- `SUMMON_AND_ADD_TAGS` foi implementado para invocar e conceder keywords ao token/lacaio invocado.
- `SUMMON_COPY` agora respeita modificações como `REMOVE_TAG` e `REMOVE_TRIGGER`. Isso corrige Kiwi, evitando loop infinito de Último Suspiro.
- `SUMMON_COPY_WITH_STATS` foi implementado para cartas como Viní Ilusório.
- `SUMMON_SELF_WITH_STAT_MODIFIER` foi implementado para efeitos de ressurreição própria com stats alterados.
- `OPTIONAL_DESTROY_AND_GAIN_ATTRIBUTES` foi implementado para Lamboinha Má Cozinheiro.
- `REPLACE_HAND_WITH_RANDOM_CARDS_FROM_DECK` foi implementado para Gusnabo de Negócios.
- `RETURN_TO_HAND_AND_MODIFY_COST` foi implementado para efeitos como Saudades.
- `FRIENDLY_MINION_COUNT_AT_LEAST` agora respeita `exclude_self`, corrigindo Niurau.
- Auras simples de `BUFF_ATTACK` em `ADJACENT_MINIONS` agora são recalculadas sem empilhar, cobrindo Memes.

Testes adicionados:

- `tests/test_lote6_summon_copy_destroy.py`

Resultado local deste lote:

```text
259 passed, 1 skipped
```


### Lote 7 — controle de mesa, sacrifício e substituição

Correções aplicadas a cartas específicas de controle de mesa:

- `RETURN_ALL_MINIONS_TO_HAND` foi implementado para Buraco Negro. Todos os lacaios voltam à mão de seus donos sem disparar Último Suspiro.
- `SACRIFICE_FRIENDLY_MINION_DESTROY_ENEMY_MINION` foi implementado para Gusnabo, o mago!, usando múltiplos alvos (`targets`: sacrifício aliado, alvo inimigo).
- `DEVOUR_FRIENDLY_MINION_GAIN_ATTRIBUTES` foi implementado para Spiid Faminto, consumindo um aliado e ganhando atributos, bônus e texto.
- `DESTROY_AND_RESUMMON` foi implementado para Renascimento, incluindo vida cheia e multiplicador de ataque.
- `DESTROY_AND_RESUMMON_FULL_HEALTH` foi implementado para Igor Insano.
- `REPLACE_FRIENDLY_MINIONS_FROM_DECK` foi implementado para Tirania. A engine escolhe deterministicamente o melhor lacaio legal do deck enquanto a UI de escolha fina não existe.
- A detecção de múltiplos alvos agora também considera o campo `source` nos efeitos das cartas.

Testes adicionados:

- `tests/test_lote7_board_control.py`

Resultado local deste lote:

```text
259 passed, 1 skipped
```


### Lote 8 — proteção, targeting e prevenção

Correções aplicadas a keywords/efeitos defensivos e de restrição:

- `CANNOT_BE_TARGETED_BY_SPELLS` foi implementado para bloquear qualquer feitiço escolhido contra o lacaio.
- `CANNOT_BE_TARGETED_BY_ENEMY_SPELLS` foi implementado para bloquear apenas feitiços inimigos, mantendo feitiços aliados válidos.
- `GRANT_TEMPORARY_SPELL_TARGET_IMMUNITY` foi implementado para Zé Droguinha, protegendo herói e lacaios aliados contra feitiços inimigos até o fim do próximo turno inimigo.
- `PREVENT_ATTACK_AGAINST_SELF` foi implementado para El Luca: o lacaio seduzido não pode atacar El Luca enquanto ele estiver em campo.
- `PREVENT_DAMAGE_THIS_TURN` foi implementado para impedir temporariamente que o lacaio afetado cause dano.
- `REDIRECT_ATTACK_TO_SELF` foi implementado para Lucas, redirecionando ataques feitos contra aliados para ele.
- `SKIP_NEXT_ATTACK` foi implementado para cartas como Vinagra; o alvo perde a próxima oportunidade de ataque e a restrição é limpa no fim do turno do dono.
- A validação de alvo agora considera se a carta jogada é feitiço, bloqueando corretamente alvos com imunidade a feitiços antes de gastar mana/carta.

Testes adicionados:

- `tests/test_lote8_protection_and_targeting.py`

Resultado local deste lote:

```text
259 passed, 1 skipped
```


### Lote 9 — custo, mana e efeitos de próximo turno

Correções aplicadas a efeitos que alteram custo, mana e compra atrasada:

- `BUFF_NEXT_PLAYED_TRIBE_HEALTH` foi implementado para Banana: a próxima Fruta jogada no turno recebe +1 de vida.
- `REDUCE_NEXT_TURN_FIRST_MINION_COST` foi implementado para La Selecione: o primeiro lacaio do próximo turno custa 1 a menos.
- `NEXT_TURN_FIRST_MINION_COST_REDUCTION` foi implementado para Vinas: o primeiro lacaio do próximo turno custa 2 a menos, ou 3 a menos se for Viní.
- `REDUCE_MANA_NEXT_TURN` foi implementado para Funkeiro: o jogador tem 1 mana disponível a menos no próximo turno, sem reduzir permanentemente o máximo de mana.
- `DRAW_CARD_TYPE` foi implementado para Tomo Amaldiçoado comprar feitiços do deck.
- `NEXT_SPELL_COSTS_HEALTH_INSTEAD_OF_MANA` foi implementado: o próximo feitiço do turno paga vida em vez de mana.
- `DRAW_CARD_DELAYED` foi implementado para AliExpress: a carta é removida do deck agora, recebe modificador de custo e chega à mão no próximo turno do dono.

Testes adicionados:

- `tests/test_lote9_cost_mana_turn.py`

Resultado local deste lote:

```text
259 passed, 1 skipped
```


### Lote 10 — efeitos de mão, deck e revelação

Correções aplicadas a efeitos que manipulam cartas na mão e no deck:

- `REVEAL_CARD_FROM_HAND` foi implementado para Viní Estudioso: o jogador escolhe um feitiço da mão, revela a carta e ela custa 1 a menos.
- `REVEAL_LEFTMOST_AND_RIGHTMOST_HAND_CARDS` foi implementado para El Gusnabito, registrando no log público as cartas das extremidades da mão alvo.
- `SWAP_RANDOM_HAND_CARD_WITH_OPPONENT` foi implementado para Tomé, trocando uma carta aleatória da mão de cada jogador no fim do turno.
- `MOVE_HAND_CARD_TO_OPPONENT_DECK_TOP` foi implementado para Spiidinho Presenteador, com escolha manual da carta e aumento de custo preservado quando ela for comprada.
- `MOVE_HAND_CARDS_TO_DECK_AND_HEAL` foi implementado para Viní Barman, permitindo escolher cartas da mão, colocá-las no deck e curar 3 por carta.
- `MOVE_ENEMY_MINION_TO_HAND_AND_SET_COST` foi implementado para Hora de Nanar, movendo um lacaio inimigo para a mão do jogador com custo definido.
- O sistema de markers de cartas modificadas no deck agora preserva modificadores relevantes quando a carta volta à mão.

Testes adicionados:

- `tests/test_lote10_hand_deck.py
- `test_lote13_recruit_resurrect_transform.py`
- `test_lote14_attack_damage_special.py
- `test_lote15_state_passives.py
- `test_lote16_hand_deck_zone_extra_mana.py
- `test_lote17_special_handlers.py
- `test_lote18_activated_abilities.py
- `test_lote19_damage_summon_play_triggers.py
- `test_lote20_combo_empower_aura.py
- `test_lote21_random_decks.py`````````

Resultado local deste lote:

```text
259 passed, 1 skipped
```


## Modo Decks Aleatórios

Além de partidas com decks construídos, o lobby agora permite criar salas no modo **Decks aleatórios**.
Nesse modo, nenhum jogador precisa escolher deck salvo: quando os dois entram e conectam na partida, o servidor gera um deck aleatório de 30 cartas colecionáveis para cada jogador.


## Lote 22 — correções de cartas reportadas em teste manual

Correções cobertas por testes:
- Hello World voltando ao deck custando 1.
- Gusneba invocando cópias com Provocar/Venenoso.
- NinjaGui 3 Anos com dois alvos em sequência.
- Vinagra concedendo +7/+7 e pulando o próximo ataque.
- Igleba invocando três tokens 1/1 com Rapidez ao morrer.
- Mao Tsé-Tung afetando campo, mão e deck.
- Nando mantendo Furtividade permanentemente.
- Viní Flamejante aplicando dano no início do turno do oponente.
- Cultista do Viní Flamejante aplicando dano no início de cada turno.
- Perfeitinha devolvendo o alvo e congelando os adjacentes originais.

Teste adicionado:
- `tests/test_lote22_manual_bugfixes.py`


## Lote 23 — auditoria de bugs prováveis

Adicionado:
- `tests/test_lote23_audit_probable_bugs.py`
- `docs/CARD_AUDIT_LOTE23.md`

Resultado local:
- `259 passed, 1 skipped`


## Lote 24 — segunda auditoria de cartas

Correções:
- Queima de Estoque não recruta mais lacaios extras além da quantidade de cartas descartadas.
- RECRUIT_MINION agora respeita `amount_source: DISCARDED_COUNT`.
- Fúria do Viní Geladinho agora causa dano aos lacaios congelados depois de congelar todos.

Teste adicionado:
- `tests/test_lote24_second_audit.py`


## Lote 25 — correções solicitadas em partida online

Correções:
- Modo Decks Aleatórios agora gera deck singleton: 30 cartas sem repetição.
- Congelar agora consome só a próxima oportunidade de ataque.
- Hover em lacaio agora mostra a carta completa.
- Spaghetti abre escolha após atacar herói inimigo.
- Mario abre escolha ao ser comprado.
- Obansug ganha a vida roubada para si mesmo.
- Lamboia permite escolher explicitamente 1ª/2ª/3ª carta.
- Ramoninho Mestre da Nerf usa cargas totais, não custo de mana.
- Dormente foi ajustado: não conta como alvo, não tem stats/Provocar ativos e acorda como recém-jogado.
- Vic Assada agora buffa apenas outras Comidas.

Teste adicionado:
- `tests/test_lote25_requested_gameplay_fixes.py`


## Lote 26 — ajustes de UI, Fortalecer e bugs reportados

Correções:
- Exibição de carta jogada pelo oponente agora dura 2,5 segundos.
- Cartas jogadas pelo oponente entram em fila de 2,5 segundos cada, sem sobreposição.
- Log de eventos agora usa sequência interna, evitando perda de animações quando o log chega ao limite de eventos.
- Hover em lacaios agora mostra a carta grande, à esquerda do mouse e acima de tudo.
- Venenoso agora tem selo visual.
- Fortalecer agora custa +1 de mana para feitiços fortalecidos.
- Nando 3 Anos não morre enquanto estiver Furtivo/Imune com 0 de vida.
- Vic só volta com o alvo quando mata o alvo marcado por ataque.
- Cardume de Peixes agora evoca 4 Peixes e adiciona 4 Peixes à mão.
- Ramoni agora reevoca com base nos stats originais: 5/3 -> 4/2 -> 3/1 -> morre.
- Lamboinha Má Cozinheiro pode ser jogado sem alvo se não houver Comida aliada.
- Troca Justa compra normalmente se não houver carta para descartar.
- Viní Estudioso revela a carta escolhida com aviso visual.
- Sagrado Rafa concede Escudo Divino a lacaios jogados, não evocados.
- Viní Religioso aumenta vida máxima do herói para 45 e permite cura acima de 30.
- Justiça usa custo dinâmico também em ações legais.
- Lamboinha Rook and How adiciona cópia 6 mana 6/6.

Teste adicionado:
- `tests/test_lote26_requested_fixes.py`


## Lote 27 — mulligan, animações, Fortalecer e bugs reportados

Correções:
- A Moeda agora existe como carta real `coin`.
- Burguês e o jogador que começa em segundo agora recebem a Moeda correta.
- Mulligan mantém 3 cartas para o primeiro jogador e 4 para o segundo; a Moeda entra após o mulligan.
- Animação de compra mostra carta grande saindo do deck, ficando em destaque e indo para a mão.
- Animação de carta jogada/revelada/queimada agora dura 4 segundos e usa fila.
- Queima por mão cheia mostra a carta queimada para ambos.
- Frontend aceita alvos `MINION` e `ANY_CHARACTER`, corrigindo Cabeçada do Viní, Ataque do Viní Mago e Bloquear.
- Viní em Disparada remove Provocar e Escudo Divino de verdade, incluindo o estado interno de Escudo Divino.
- Fortalecer aparece como `Fortalecer (+1)` e continua validado no backend.
- Guilãozinho agora tem evento de revelação compatível com a UI.

Teste adicionado:
- `tests/test_lote27_mulligan_ui_and_card_fixes.py`


## Lote 28 — preload de imagens, animações de compra e correções de cartas

Correções:
- Lobby e tela de partida agora pré-carregam imagens de cartas/heróis com barra de carregamento.
- A tela de partida carrega cartas auxiliares com `include_tokens=1`, permitindo renderizar a Moeda.
- Compra automática do começo do turno usa animação lenta de 3s.
- Compras por efeito no meio do turno usam animação rápida de 1s.
- Compra do oponente usa animação curta sem mostrar carta no centro da tela.
- Hover de lacaio só mostra a carta depois de 1,5s parado.
- Moldura normal do lacaio ficou mais fina; moldura verde de ataque ficou mais grossa.
- Moeda foi consolidada como carta real `coin`; `moeda` vira alias.
- Burguês adiciona a Moeda correta.
- Spaghetti mostra opções legíveis após atacar.
- Vagner Pikachu agora permite escolher os três alvos sequenciais.
- Iglu Atleta, após já ter atacado, ganha nova oportunidade apenas contra lacaios inimigos.
- Viní Formoso recebeu arquivo `static/cards/vini_formoso.png` como placeholder, pois a arte real não estava no pacote.

Teste adicionado:
- `tests/test_lote28_preload_draw_coin_and_cards.py`
