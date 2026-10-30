# Dancinha (Reels): resultado dos testes de 30/10/2026

Copiar movimento da Naiaflow, 5 s, personagem luna, 6 créditos cada (1 por segundo + 1 do clone).

| Fonte | Arquivo | Resultado |
|---|---|---|
| Caminho B (foto como 1º quadro) | `conteudos/dancinha_b.mp4` | "Muito IA": gira devagar, não dança. Descartado. |
| Biblioteca nº 7 | `conteudos/dancinha_a_biblioteca7.mp4` | Movimento natural; mãos borram nos gestos rápidos; copia roupa e cozinha da referência. |
| Link do TikTok | `conteudos/dancinha_link.mp4` | Movimento muito natural; mão borrada no 1º meio segundo (cortar no PC); copia roupa e banheiro. |
| Enviar vídeo (arquivo do administrador) | `conteudos/dancinha_envio.mp4` | Melhor nitidez; movimento natural; rosto estável; copia roupa e cenário. |

| Enviar vídeo + Roupa e Cenário | `conteudos/dancinha_roupa.mp4` | Roupa trocou certinho (biquíni animal print da galeria), estável nos 5 s; dança igual. **Cenário não trocou**: continuou o quarto da referência (foto enviada era da Luna na piscina). |

## Conclusões

- A qualidade vem do **vídeo de referência**, não da fonte. O custo é o mesmo (Naia confirmou).
- Sem mexer em **Roupa** e **Cenário**, o vídeo copia a roupa e o lugar da referência. Esses botões aceitam uma foto de referência da roupa e do lugar; não mudam o custo nem o movimento (Naia).
- Referência ideal (Naia + testes): 5 a 12 s; corpo inteiro, centralizado; uma pessoa; câmera parada; luz uniforme; boa resolução; sem texto na tela; movimentos moderados; evitar giros, gestos muito rápidos e roupa transparente.
- Vídeo de criadora real: trocar Roupa e Cenário para não virar cópia do vídeo dela.
- **Roupa**: botão abre menu "Abrir galeria / Copiar do vídeo". Galeria tem "Minhas roupas" e categorias (Praia, Academia, Baile funk, Lingerie…); escolher clicando na imagem da peça. Não muda o custo (6).
- **Cenário**: mesma galeria; a seção "Cenários" não tem catálogo, só "Enviar do dispositivo". Uma foto com a Luna na piscina foi ignorada; próximo teste: foto do lugar **sem pessoa**.

## Fluxo recomendado (a automatizar)

1. Administrador manda o vídeo da trend (arquivo ou link).
2. PC corta o melhor trecho de frente (5–8 s), de graça.
3. Naiaflow: Enviar vídeo → luna → Roupa/Cenário com fotos da Luna (1 Criar, 1 crédito) → Sem som (música do Instagram) ou Som original.
4. PC corta o começo com defeito e tira um quadro para o Stories (grátis).
5. Aprovação no Telegram.

Script de teste: `worker/ferramentas/teste_dancinha_a.py` (`--biblioteca=N`, link, `--arquivo=video.mp4`, `--saida=nome`, `--gerar`). Escolhe o clone luna quando não vem marcado e só gera com "Gerar vídeo 6" ligado.
