# Guia de estilo do Instagram da Luna

Feito em 29/10/2026 a partir de um perfil de referência escolhido pelo administrador: uma garota praiana mostrando a rotina, com 156 mil seguidores. Foram lidos os 239 posts do Feed e os 72 Reels.

- **O que copiamos:** o estilo.
- **O que não copiamos:** a pessoa, as fotos e a política.

O texto que a IA segue fica em `STYLE_GUIDE`, no arquivo `frontend/api/_assistant.ts`. Ele vale para a Conversa e para o Planejamento (redes). O +18 do Telegram segue as regras dele.

## O que a referência faz

### Feed

**Formato:** praticamente tudo é **carrossel**. Cada carrossel é **o mesmo momento** (mesma roupa e mesmo lugar) em 2 a 4 poses:
1. de frente, sorrindo;
2. de lado ou de costas, olhando por cima do ombro;
3. mão no cabelo ou mais perto da câmera;
4. pose divertida: paz, piscadinha ou beijo.

**Visual:** cara de **foto de celular**. Selfie no espelho, foto tirada por amiga, luz natural, flash à noite.

**Temas, do mais comum para o menos comum:**
- praia de biquíni;
- piscina, deitada na espreguiçadeira;
- selfie no espelho de short ou cropped;
- academia;
- vestido justo à noite (barzinho, jantar);
- em casa (sofá, cama);
- carro;
- jetski e lancha;
- tbt de viagem.

**Legenda:** uma frase curta, quase sempre uma **pergunta provocante ou fofa** que puxa comentário, ou um trecho de música. Leva 1 a 3 emojis e algumas hashtags.
- Exemplos de frase: "Me levaria pra uma pescaria? 👀❤️", "Fiquei bem nesse vestido?", "Passaria bronzeador em mim? ❤️🌞".
- **Hashtags mais usadas:** #girl (42), #fy (36), #praia (29), #viral (26), #explore (24), #fyp (24), #verão, #tbt, #academia.

**Ritmo:** cerca de **1 a 2 carrosséis por dia**.

### Reels

**Quantidade:** 72 Reels, quase **um por dia**, de 15 mil a 390 mil visualizações.

**Formato:** ela de frente para a câmera, com **música em alta**.
- Dancinha: arrocha, funk ou a trend do momento.
- "Entramos na trend".
- Passinho na academia.
- Mostrando o look ou o biquíni.
- Respondendo uma **caixinha de perguntas**, com o texto na tela.

**Legenda:** curtíssima, por exemplo "Entramos na trend", "Arrocha seu safado", "Essa eu gostei". Usa #viral #fyp #trend.

### Perfil

**Bio:** 3 linhas curtas com emoji: *"Praiana por natureza / Um pedacinho da minha rotina / Destaque Especial"*.

**Destaques:** Especial 🌶️, Só Meu, Praia, Perguntinhas e Academia.

## O que muda na Luna

1. **Feed:** sempre carrossel de 2 fotos.
   - O "Gerar esta foto" já vem com 2.
   - O robô gera a 2ª foto com pose diferente, na mesma roupa e no mesmo lugar (`CAROUSEL_POSES` em `frontend/api/_worker.ts`).
   - A foto entra **inteira** no Feed, com as laterais desfocadas.
2. **Prompts:** a Conversa e o Planejamento escrevem nos temas acima, com cara de celular. As legendas são uma pergunta curta com emoji.
3. **Ritmo:** **um post por vez**, no máximo 1 carrossel por dia, em horários diferentes. Nunca publicar tudo de uma vez.
4. **Reels:**
   - **Grátis:** foto que vira vídeo com zoom leve e uma música em alta da biblioteca do Instagram.
   - **Com movimento (dancinha, trend):** usa vídeo na Naiaflow ("Copiar movimento"), que gasta mais crédito. Fica para quando o administrador pedir.
5. **Fora:** política (candidato, eleição, número de campanha), marcas e qualquer cópia da pessoa da referência.

## O que a Naia (IA da Naiaflow) ensinou (29/10/2026)

Perguntas feitas só para obter informação. Nada foi gerado e os créditos ficaram em 11.

**Prompts**
- **A identidade vem das fotos do clone, não do texto.** Escreva a **cena**, nunca a pessoa: roupa, lugar, luz, pose, expressão e enquadramento.
- Descrever rosto, cabelo, pele, idade ou corpo, ou usar palavras como "jovem" e "loirinha", faz a Luna sair diferente.
- **Roupa completa:** diga as duas partes, a de cima e a de baixo. Pedir só uma peça faz faltar a outra (foi o caso da foto de topless).
- **Enquadramento explícito:** "corpo inteiro dentro do quadro, da cabeça aos pés". Escrever só "corpo inteiro" costuma cortar os pés.
- **Mãos simples:** na cintura, no cabelo, no bolso, segurando o celular. Gestos complicados perto do rosto saem com defeito.
- **Idioma:** português funciona bem. Inglês não é necessário.

**Opções do composer**
- **Celular:** dá cara de foto de celular. É a principal para o estilo da Luna.
- **Melhorar:** deixa a foto "perfeita" e produzida, o contrário do natural.
- **Combinações sugeridas:** Celular + Luz suave, ou Celular + Maquiagem leve para um Feed mais cuidado.

**Carrossel**

| Caminho | Custo | Observação |
|---|---|---|
| Foto por foto com o mesmo prompt | 1 crédito por foto | Roupa e lugar podem mudar entre as fotos. |
| **Carrossel do Insta** | 3 créditos | Parte de uma foto base e entrega 3 variações do mesmo look e lugar. É o mais consistente. Não usa o texto do prompt. |
| Multi ângulos | 3 créditos | Entrega 10 imagens, mais voltadas a ângulos de câmera do que a poses diferentes. |

**Reels de dancinha**
- Usa **Copiar movimento** com um vídeo de referência da trend, que precisa estar na galeria da Naiaflow ou ser enviado no card.
- **Custo:** 1 crédito por segundo + 1 pela Luna. Exemplos: 5 s = 6 créditos, 8 s = 9 créditos.
- "Usar como primeiro quadro" não serve para dancinha.
