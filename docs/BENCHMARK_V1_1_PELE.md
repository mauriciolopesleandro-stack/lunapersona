# Persona Engine V1.1: pele natural (anti-uncanny valley). Benchmark A/B/C/D

Data: 2026-10-05.
Branch: `feature/persona-v1.1-natural-skin`. **NÃO publicado na main.**
Hardware: RTX PRO 4000 Blackwell 24 GB, US$ 0,57/h, EU-RO-1.
Scripts: `scripts/benchmark_v11.py`, `scripts/analyze_v11.py` e `scripts/run_benchmark_v11.sh`.
Dados brutos: `docs/testes/bench_v11_results.json`.
Folha de rostos: `docs/testes/bench_v11_rostos.jpg`. Está fora do git, porque o `.gitignore` ignora `*.jpg`.

## Diagnóstico

O aspecto plástico nasce na troca de cabeça (Qwen 2511 + Lightning 8 passos + BFS).

- **O que se vê:** na folha de rostos, a pele do Z-Image + LoRA (antes do Qwen) é fosca, com sardas e poros. Depois do Qwen ela fica:
  - lisa e brilhante;
  - com aspecto de maquiagem, com contorno, delineado e boca marcada.
- **A origem provável:** a cabeça redesenhada a partir da master_face, em poucos passos (Lightning), num rosto pequeno (~150 px). O CFG é 1, então o negativo não tem efeito nenhum.
- **A idade:** a média ficou em 24 anos, com alvo de 27. É a deriva já conhecida da V1.

## O que foi testado

Todas as configurações usaram as mesmas 10 cenas brasileiras, as mesmas sementes (7101–7110), as mesmas masters (sha256 conferido), a mesma LoRA a 1.0, 832×1216 e uma tentativa por cena.

| Config | O que muda |
|---|---|
| A | V1 de produção. Só mede a pele. |
| B | A + texto de preservação de textura **e** trava de idade (27) no prompt do Qwen. |
| C | Imagens de A + analisador + correção condicional. Corrige se a nota for < 0,50, com Z-Image sem LoRA, denoise 0,25, só o recorte do rosto. |
| D | Imagens de B + a mesma correção. |

C e D reaproveitam a cena e o Face Lock de A e B, que são idênticos por construção. O tempo e o custo deles são os de A/B mais a correção.

## Resultados

| Configuração | Face | Pele | Idade | Pose | Corpo | Pessoas | Tempo | Custo |
|---|---|---|---|---|---|---|---|---|
| A: V1 | 0,811 (mín 0,631) | 0,711 (8 PASS, 2 FAIL) | 24,3 | n/a (modo livre) | NOT_COMPARABLE | 10/10 PASS | 131 s/img | US$ 0,0208 |
| B: V1 + texto | 0,809 (mín 0,640) | 0,672 (6 PASS, 3 WARN, 1 FAIL) | 24,6 | n/a | NOT_COMPARABLE | 10/10 PASS | 129 s/img | US$ 0,0204 |
| C: A + correção | 0,811 (= A) | 0,711 (= A) | 24,3 | n/a | NOT_COMPARABLE | 10/10 PASS | 154 s/img | US$ 0,0244 |
| D: B + correção | 0,809 (= B) | 0,672 (= B) | 24,6 | n/a | NOT_COMPARABLE | 10/10 PASS | 162 s/img | US$ 0,0257 |

- **Aceitas e falhas:** 10/10 aceitas em todas as configurações, sem falhas e sem retries (max_attempts = 1).
- **VRAM máxima:** 23,7 GB no nvidia-smi; 21,8 GB por etapa.
- **Tempo total de GPU:** 53 min (1311 + 1286 + 232 + 338 s), cerca de US$ 0,50.
- **Pose:** foi pedida em modo livre, então não há pose para comparar.
- **Corpo:** NOT_COMPARABLE porque as cenas não estão na pose da master de corpo.

### Correções tentadas: todas descartadas pela IdentityPreservationGuard

| Config | Cena | Rosto antes → depois | Idade | Pose (dist.) | Pele antes → depois | Motivo |
|---|---|---|---|---|---|---|
| C | 2 | 0,789 → 0,686 | 23 → 25 | 0,000 | 0,12 → 0,06 | identidade −0,103, pele não melhorou |
| C | 5 | 0,828 → 0,677 | 24 → 28 | 0,004 | 0,28 → 0,99 | identidade −0,151, idade +4 |
| D | 1 | 0,818 → 0,688 | 23 → 26 | 0,021 | 0,44 → 0,64 | identidade −0,130 |
| D | 2 | 0,776 → 0,662 | 23 → 26 | 0,001 | 0,19 → 0,30 | identidade −0,114 |
| D | 5 | 0,830 → 0,663 | 25 → 27 | 0,000 | 0,35 → 1,00 | identidade −0,167 |
| D | 6 | 0,861 → 0,714 | 23 → 23 | 0,006 | 0,34 → 0,34 | identidade −0,147 |

**Leitura visual:** as correções deixam a pele visivelmente mais natural, com sardas, poros e menos brilho. O custo é parecer outra pessoa: o rosto volta a se parecer com o Z-Image sem a LoRA. Pose, corpo e pessoas não mudaram. A guarda fez o que a regra manda e descartou todas.

## Regressão V1 × V1.1

- Com a ficha como está (recursos da V1.1 desligados), a V1.1 gera pelo mesmo caminho da V1. O teste confirma que o grafo do Qwen é idêntico ao da V1 quando não há texto extra.
- A config A deu 10/10 aceitas, com rosto médio 0,811. A geração real da V1 tinha dado 0,749, com 1 amostra.
- Testes: 123 antigos passando, sem nenhuma alteração neles, mais 33 novos (156 no total, 0 falhas).

## Decisão

**Não promover nada.** A ficha continua 1.0 e os recursos da V1.1 continuam desligados.

- **B (texto no Qwen):** não melhorou. Visualmente ficou igual a A, e a nota da pele caiu um pouco (0,711 → 0,672). Com CFG 1 e Lightning, o texto quase não pesa.
- **C/D (correção):** melhora a pele a olho, mas tira de 0,10 a 0,17 da identidade. Isso viola a regra "realismo não pode destruir identidade". Ficaram 0/6 correções aceitas.
- **Merge na main:** só quando houver melhora real e medida.

## Limitações

1. **O SkinRealismScore não acompanha o olho.** Ele confunde brilho e reflexo (especular) com textura:
   - imagens A brilhantes tiraram 0,9–1,0;
   - a correção da cena 2 da C, que visualmente tem mais textura, tirou nota menor.

   A escala é PROVISÓRIA e a confiança é LOW. O score serve como telemetria, não como juiz. Precisa de calibração com rótulos humanos e de uma medida de brilho especular.
2. **Aparência CGI, porcelana e simetria artificial não são medidas.** Ficam como UNKNOWN.
3. **A idade estimada (InsightFace) tem ruído de ±3 anos.** A trava de idade não mudou a média (24,3 → 24,6).
4. **Amostra:** 10 cenas, sem retry, e só em modo livre.
5. **C/D reaproveitam as imagens de A/B.** Mede-se só o efeito da correção, que era o objetivo.

## Próximos passos (cada um precisa de benchmark antes)

1. **Correção que preserve a identidade:**
   - **Separação de frequências:** levar para a imagem do Qwen só o detalhe fino da correção, mantendo forma e cor do Qwen. Não usa modelo novo e é barato.
   - **Ou corrigir com a LoRA da Luna a 1.0:** a mesma força da cena, sem aumentar nada.
   - **Ou baixar o denoise para 0,12–0,18.**
2. **Melhorar o medidor:**
   - descontar o brilho especular;
   - calibrar lo/hi com uns 20 rostos rotulados (natural × plástico).
3. **Rever a master_face:** se a própria master tem maquiagem forte, o Qwen copia isso.
