# Plano de implantação: Luna V2 no site

Data: 2026-10-06. Base: branch `feature/persona-v2-realvis-lustify`.
A Luna está travada em `luna-face-v2.0`. A descrição completa está em `personas/luna/luna_v2_travada.json`.

## Objetivo

O site passa a ter **uma tela principal simples**, onde a Luna e o motor V2 já vêm escolhidos:

1. **Sem foto:** você descreve a cena e recebe fotos da Luna com cara de celular. É o que já funciona hoje na V2.
2. **Com foto de referência:** você sobe uma foto qualquer. O sistema **lê** a foto (pessoa, pose, roupa, ambiente, luz, ângulo da câmera) e devolve a **mesma foto com a Luna no lugar da pessoa**: mesma pose, mesmo jeito, mesmo ambiente e mesma luz.

As duas formas seguem as mesmas regras:
- **Sem filtros artificiais:** nada de granulado, cor lavada ou desfoque que não estejam na foto original.
- **Mesmo negativo:** o negativo que já está no sistema (global, persona, V2 e estilo).
- **Passadas extras quando preciso:** 3 de rosto (a 1ª com InstantID) e 2 de corpo, com rollback.
- **Rosto travado:** imagem final abaixo de 0,70 não é entregue como Luna. Ela é refeita sozinha com outra semente, no máximo 2 vezes.

---

## O que já existe e será reaproveitado

| Peça | Onde está hoje | Uso na V2 |
|---|---|---|
| Descrição da foto (Florence-2 PromptGen, "more detailed caption") | `workflows/describe-image.json` (V1) | leitura da referência |
| Achar a mulher e a cabeça na foto (Florence-2) e recortar a pessoa (SAM2) | `services/person_swap.py` (V1) | máscara da pessoa a substituir |
| Rostos, sexo, idade e semelhança (LunaFaces) | nó do estúdio | escolher a pessoa certa e validar |
| Pose (DWPose) | nó do estúdio | copiar a pose e conferir no fim |
| Multi-pass com rollback, InstantID e trava do rosto | branch V2 | identidade e realismo |
| Negativo em camadas | `config/persona_engine.json` + `persona_engine_v2.json` | igual |
| LLM de texto que já está no pod (Qwen3, Ollama) | V1 | organizar a leitura da foto em campos. **Nenhum modelo de LLM novo será baixado.** |

---

## Fases

### Fase 0: trava e documentação (feita)
- Rosto travado com impressão sha256.
- `luna_v2_travada.json` e `LUNA_V2_OUTRA_MAQUINA.md`.
- 219 testes passando.

### Fase 1: leitura inteligente da foto de referência
Cria um "leitor de referência" que devolve uma **ficha da foto**:
- **Pessoas:** quantas há, qual é a que vira Luna (mulher de maior destaque) e quem são as outras, que ficam intactas.
- **Pose:** esqueleto do DWPose e direção da cabeça.
- **Roupa e acessórios, ambiente e objetos na mão:** vêm da descrição do Florence. O Qwen3 do pod organiza a descrição em campos.
- **Luz:** medida nos pixels (claro ou escuro, quente ou fria, flash ou luz de janela), não inventada.
- **Câmera:** selfie, espelho ou foto tirada por outra pessoa; enquadramento.

Testes locais e depois **5 fotos suas no pod (~US$ 0,03)** para conferir se a leitura bate com o olho.

### Fase 2: modo "replicar foto" no motor V2
1. Recorta a pessoa escolhida (SAM2). O **resto da foto não é tocado**: ambiente, luz e cores ficam iguais aos pixels originais.
2. Redesenha só a pessoa com RealVisXL + LoRA da Luna, seguindo a **pose da foto** (esqueleto) e a **roupa lida**.
3. Roda as passadas travadas: rosto 1 com InstantID, rosto 2, rosto 3 sem LoRA, corpo 1 e corpo 2.
4. **Ajuste de cor só para encaixar:** a cor da pessoa nova é casada com a da foto original em volta. Não é filtro: o objetivo é ela não parecer colada.
5. **Validação:**
   - rosto ≥ 0,70;
   - pose parecida com a da foto (distância ≤ 0,15);
   - fundo intacto, com pixels fora da pessoa ≥ 99% iguais;
   - uma só Luna.

**Ponto a decidir:** para segurar a pose no SDXL é preciso um ControlNet de pose para SDXL, um modelo novo de ~2,5 GB (por exemplo, o union do xinsir). Antes de adotar, comparo com a alternativa sem ele (denoise moderado em cima dos pixels originais).
**Benchmark:** 5 fotos suas, 2 variações cada, **~US$ 0,30**, só com sua autorização.

### Fase 3: API no servidor
- **Rotas novas:**
  - `POST /api/v2/generate` (texto ou foto, quantidade de 1 a 4);
  - `GET /api/v2/jobs/{id}` (andamento por passada);
  - histórico.
- **Fila:** em lote. Todas as bases primeiro, depois as passadas, para o modelo não ser recarregado.
- **Mantido da V1:** o pod fica acordado enquanto há fila e o aviso no Telegram continua funcionando.
- **Persona perdida:** refaz sozinho com outra semente (no máximo 2 vezes). Se não der, a foto aparece como "descartada", nunca como Luna.
- **Custo e tempo:** registrados por foto.

### Fase 4: site simplificado (celular primeiro)
**Uma tela, "Criar foto":**
- `[ Foto de referência (opcional) ]` com a ficha lida aparecendo embaixo, para conferir;
- `[ O que você quer (opcional) ]`;
- `[ Quantas: 1 2 3 4 ]`;
- `[ Gerar ]`;
- estimativa de tempo e custo antes de gerar.

**Resultado:**
- cartões com a foto, um selo **"Luna ✓ 0,80"**, e os botões baixar e refazer;
- as descartadas ficam em "ver descartadas".

**Padrões:** a persona é a Luna e o motor é a V2. A escolha de modelo, workflow e limiar sai da tela.
**Avançado:** as telas antigas (V1, pack, vídeo, voz) ficam num menu "Avançado", sem sumir.

### Fase 5: infraestrutura do pod
Os modelos da V2 somam ~11 GB: RealVisXL (6,9 GB), InstantID (4,2 GB) e, opcionalmente, o ControlNet de pose (2,5 GB). Hoje o volume está quase cheio. **Ponto a decidir:**

| Opção | Custo | Efeito |
|---|---|---|
| A. Baixar a cada vez que o pod liga (disco temporário), como nos testes | ~70 s a mais por ligação (~US$ 0,01) | nada muda no volume |
| B. Liberar espaço no volume, apagando modelos que a V2 não usa | grátis | inicia mais rápido; precisa da sua lista de "pode apagar" |
| C. Volume maior | custo mensal do volume | mais simples a longo prazo |

### Fase 6: implantação segura
1. Todos os testes e o build do site passando.
2. **Teste completo antes da main:** o site em versão de prévia (preview do Vercel) e o pod rodando o código da branch numa sessão de teste.
3. **Homologação sua no celular:** ~10 fotos. Critério: rosto ≥ 0,70 em pelo menos 8 de 10, pose e ambiente iguais aos da foto, sem filtro.
4. **Só depois, e com o seu ok explícito, entra na `main`.** Antes disso, o Claude Code precisa da sua permissão para publicar (o modo automático bloqueia). A V1 continua lá.
5. **Volta rápida:** uma chave `motor_padrao: v1|v2` na configuração devolve o site para a V1 sem novo deploy.

---

## Estimativas

| Item | Valor |
|---|---|
| Tempo por foto (V2, 5 passadas) | ~2,5–3 min (lote) |
| Custo por foto | ~US$ 0,03 |
| Benchmarks de desenvolvimento (fases 1 e 2) | ~US$ 0,35, cada um só com autorização |
| Pod ligado a cada uso | ~1–2 min para acordar |

## Riscos

- **Corpo diferente:** se a pessoa da foto tem um corpo muito diferente do da Luna, o redesenho pode distorcer a roupa. Isso vira critério da homologação.
- **Várias pessoas ou espelhos:** é preciso escolher a pessoa certa e não duplicar a Luna. A V1 já tem essa lógica, que será reaproveitada.
- **Mãos e objetos na mão:** podem mudar. Ficam fora da máscara quando possível.
- **Foto pequena ou borrada:** o resultado herda a baixa qualidade. O site avisa.
- **Conteúdo:** o negativo do sistema (inclui "nude", "nsfw") continua valendo, mesmo que a foto de referência mostre outra coisa.

## Decisões que preciso de você antes de começar

1. **Modo padrão com foto:** "trocar a pessoa e manter a foto" (recomendado, porque replica ambiente e luz exatamente) ou "recriar a cena parecida"?
2. **Modelos no pod:** opção A, B ou C da Fase 5.
3. **ControlNet de pose para SDXL:** autorizar o teste com o modelo novo (~2,5 GB, grátis para baixar, só custo de GPU no benchmark)?
4. **Quantidade padrão por clique:** 1 ou 2 fotos?
5. **Benchmarks das fases 1 e 2:** autorizar até ~US$ 0,35, mandando 5 fotos de referência suas?
