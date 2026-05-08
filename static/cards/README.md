# Imagens das cartas

Coloque aqui as imagens das cartas seguindo este formato:

- Nome do arquivo: `{card_id}.png` ou `{card_id}.jpg` ou `{card_id}.webp`
- Recomendado: 512×512 pixels ou maior, formato PNG ou WebP
- O `card_id` é o `id` da carta no `game/data/cards.json`
  (ex.: `vini_zumbi.png`, `corrompido_caolho.jpg`)

## Como funciona o fallback

Se a imagem da carta não for encontrada, o jogo mostra automaticamente um
placeholder com a inicial do nome num círculo colorido pela tribo. Não é
preciso ter as 240 imagens prontas — adicione-as aos poucos.

## Imagens dos heróis (avatares dos jogadores)

Coloque-as em `static/heroes/{username}.png` (sem extensão no nome de
usuário). Ex.: `lucas.png`, `marina.jpg`. Caso não exista, o avatar usa a
inicial do nome.

## Listagem automática

O servidor expõe `GET /api/card-images` que devolve a lista de `card_id`s
que têm imagem disponível. O cliente consulta esse endpoint na carga e
decide quando mostrar a imagem ou o fallback, evitando requests 404 no
console.
