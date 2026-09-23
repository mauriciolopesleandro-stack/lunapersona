# Plano: treinar uma LoRA da Luna para o Chroma1-HD

Rascunho para quando formos treinar a identidade da Luna como LoRA (em vez
de so texto no prompt). Nada disso foi executado ainda - é so o roteiro.

## 1. Dataset

- Ponto de partida: as 26 imagens em Drive > "Luna" > "Dataset aprovado -
  treino LoRA Lunavox" (so 4 foram baixadas para `personas/luna/references/`
  ate agora). Ver `LEIA-ME.txt` dessa pasta.
- Atencao: as 26 sao sinteticas, todas derivadas de UM retrato-base via FLUX
  Kontext - variedade real de angulo/pose/iluminacao pode ser baixa. Se a
  LoRA sair fraca em angulos diferentes do retrato original, o proximo passo
  e gerar mais variacoes (poses, angulos, fundos diferentes) a partir desse
  mesmo retrato antes de treinar de novo, ou fotografar/gerar material novo
  com mais variedade deliberada.
- Recomendado 20-40+ imagens variadas para um resultado solido; 26 e o
  minimo aceitavel para uma primeira tentativa.
- Resolução: normalizar para o que a ferramenta de treino pedir (tipicamente
  multiplos de 64, ex. 1024x1024 ou buckets variados).

## 2. Legendas (captions)

- Uma legenda por imagem, descrevendo SO o que varia (roupa, pose, cenario,
  iluminacao, expressao) - nunca as caracteristicas fixas (rosto, cor de
  cabelo, tom de pele), para a LoRA aprender "isso e a Luna" em vez de
  memorizar uma cena especifica.
- Usar uma palavra-gatilho consistente (ex. "lunavox woman" ou similar) no
  inicio de cada legenda, em ingles (Chroma responde melhor em ingles - ver
  ajuste ja feito no assistente de chat).
- Pode gerar automaticamente com um captioner (ex. Florence-2, JoyCaption) e
  revisar a mao, ou escrever manualmente - com 26 imagens da pra fazer a mao.

## 3. Ferramenta de treino

Duas opcoes usadas pela comunidade para Chroma, ambas por linha de comando:

- **ai-toolkit** (Ostris) - suporte a Chroma, configuracao via YAML, mais
  simples de comecar.
- **diffusion-pipe** - tambem suporta Chroma, mais flexivel/avancado.

Escolher uma na hora (ai-toolkit e o ponto de partida mais comum).

## 4. Treino

- Roda na L4 do pod (24GB) - Chroma cabe em VRAM menor que FLUX dev cheio.
- Tempo estimado: algumas horas a uma noite inteira, dependendo de
  passos/dataset - GPU fica ocupada e cobrando o tempo todo.
- Rank da LoRA tipico: 16-32 para personagem.
- Salvar checkpoints intermediarios para comparar (nem sempre o ultimo passo
  e o melhor - overfitting em dataset pequeno e um risco real aqui).

## 5. Uso no ComfyUI

- Depois de treinada, a LoRA e um arquivo pequeno (`.safetensors`, poucos
  MB a algumas centenas de MB) que entra via `LoraLoaderModelOnly` (ou
  `LoraLoader` se afetar tambem o CLIP) no workflow `chroma-txt2img.json`,
  entre o `UNETLoader` e o `ModelSamplingAuraFlow`.
- Adicionar um novo workflow (ex. `chroma-lora-luna.json`) ou parametrizar o
  existente com um placeholder `${LORA_NAME}` opcional.
- Registrar a LoRA em `models/registry.json` ou num campo novo no
  `persona.json` da Luna (`generation.lora_id`), para o backend saber
  aplicar automaticamente quando a persona for a Luna.

## 6. Validacao

- Gerar um lote de imagens de teste com prompts variados (roupa, cenario,
  angulo diferentes dos exemplos de treino) e comparar contra as referencias
  originais - se a identidade nao generalizar bem, ajustar dataset/captions
  e re-treinar.

---
Nada aqui foi executado. Quando for a hora, seguir esse roteiro passo a
passo com o pod ligado, comecando pelo dataset.
