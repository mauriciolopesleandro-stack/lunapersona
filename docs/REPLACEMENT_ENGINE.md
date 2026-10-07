# Replacement Engine (V2)

Troca a pessoa de uma foto pela Luna e mantém a **fotografia**: enquadramento, pose, perspectiva, roupa, cenário e luz.
O que é da pessoa original sai: rosto, cabelo, tatuagens e marcas.

Código: `backend/app/core/engines/replacement.py` (núcleo, sem ComfyUI), `backend/app/providers/comfyui/sdxl_engine.py` (adapter),
`backend/app/providers/comfyui/engines_v2.py` (fábrica), `config/replacement_engine_v2.json` (prompts, negativos, limites).
API: `POST /api/v2/replace` (ver `docs/PERSONA_ENGINE.md`).

## Fluxo

```
foto original
 1. análise da cena ............ rosto-alvo, corpo, outras pessoas (ComfyReferenceReader)
 2. segmentação ................ pessoa principal x secundárias, cabelo, roupa (Florence), acessórios
 3. pose ....................... DWPose da foto ORIGINAL
 4. profundidade ............... Depth Anything V2 Small da foto original (na tatuagem: da imagem LIMPA)
 5. proteção ................... roupa, fundo e acessórios ficam fora da máscara
 6. identidade ................. LoRA lunavox (força fixa) + master facial + referências
 7. reconstrução RealVisXL ..... rosto + cabelo + pescoço (ControlNet Union: pose + profundidade)
 8. refino do rosto ............ InstantID com a master (só se a identidade ficar abaixo do limite;
                                  MAX_QUALITY sempre). Não é troca crua de rosto.
 9. refino do corpo ............ faixa de transição pescoço/ombros (MAX_QUALITY ou degrau F+)
10. limpeza de tatuagem ........ zona da tinta preenchida antes; profundidade da imagem LIMPA; sem LoRA;
                                  tom casado com a pele conhecida ao redor; mede tattoo_residual_score
11. cabelo/mão/borda ........... transição suave só para DENTRO da máscara
12. integração ................. degrau de tom pele-pele na borda, harmonia rosto/corpo parcial,
                                  só o grão que falta (sem filtro, sem grão pesado)
13. validação V2 ............... por dimensão (PASS/WARN/REJECT, score, limite, motivo)
14. aceitar / tentar de novo (correção específica) / reprovar
```

Garantia de isolamento: depois de cada passe, **tudo o que está fora da máscara volta a ser a foto original**, pixel a pixel.
Os testes conferem isso sujando a imagem fora da máscara no adapter falso.

## Modos

| Modo | O que liga |
|---|---|
| FAST | sem profundidade, 25 passos |
| QUALITY (padrão) | pose + profundidade + referência facial + limpeza de tatuagem + integração, 30 passos, até 2 novas tentativas |
| MAX_QUALITY | + refino do corpo + hi-res do rosto (1536), 40 passos, refino de rosto sempre, até 3 novas tentativas |

Degraus do benchmark (`policies.LADDER`):

| Degrau | Liga |
|---|---|
| A | nada |
| B | + refino + referência facial |
| C | + pose |
| D | + profundidade |
| E | + segmentação |
| F | + corpo + hi-res |
| G | + tatuagem |
| H | + integração |

## Validação (por dimensão)

| Dimensão | Mede | PASS / REJECT |
|---|---|---|
| identity | semelhança ArcFace com a Luna | ≥ 0,70 / < 0,50 |
| original_residual | semelhança com o rosto ORIGINAL, tinta e cabelo que sobraram | rosto < 0,30 (rejeita ≥ 0,40); tinta ≤ 0,03 (rejeita > 0,15); cabelo ≤ 0,08 (rejeita > 0,30) |
| tattoo | `tattoo_residual_score` (fração da tinta original que ficou) | ≤ 0,03 / > 0,15 |
| pose | distância da pose original | ≤ 0,12 / > 0,25 |
| background | fração alterada fora da pessoa | ≤ 0,002 / > 0,02 |
| skin | textura (0,55 a 1,9 da original) e tom rosto × corpo (ΔE ≤ 6, aviso até 10) | |
| body | largura de ombros final/original | ± 8 % |
| duplicate_persona | Luna aparece mais de uma vez | pessoas ao fundo são permitidas |
| anatomy | | UNKNOWN: ainda não há detector de mãos/dedos, conferir no olho |
| composition | deslocamento global | ≤ 0,01 / > 0,05 |

Falhas graves (REJECT direto): rosto não encontrado, rosto original sobrando, fundo alterado, Luna duplicada.

## Nova tentativa (por tipo de falha)

| Falha | Correção |
|---|---|
| identidade | mais referência facial (até 0,6) e mais denoise no rosto; refino forçado. **A LoRA não sobe.** |
| tatuagem | margem maior e mais denoise na zona |
| pose | ControlNet de pose mais forte |
| fundo | máscara encolhida e denoise menor |
| corpo | liga o refino do corpo |
| pele | liga a integração |

Fica sempre a melhor tentativa. O número de tentativas é limitado pelo modo.

## Telemetria

Cada job registra:
- identificação: job_id, persona_id, engine, mode, model, provider, engine_version, workflow_versions, license;
- modelos: checkpoint_hash, lora_hash;
- parâmetros: seed, steps, cfg, resolution, controlnet_strength, reference_strength, denoise, passes;
- desempenho: vram_peak_mb, duration_s, gpu_seconds, estimated_cost_usd;
- resultado: validation_result, failure_reason, retry_count, retries.

No backend, a telemetria vai também para `logs/engines_v2_telemetry.jsonl`.

## Retenção e privacidade

- Processamento **só local**: o ComfyUI roda no próprio pod e nenhuma imagem vai para API externa. A API recusa `processing` diferente de `local`.
- Intermediárias (`repl_*`) e uploads (`v2in_*`) são apagados depois de 24 h. Resultados finais (`repl_final_*`, `repl_faceswap_final_*`) ficam 30 dias. Ver `config/engines_v2.json`.
- Masters são imutáveis (sha256 na Persona Sheet). Nenhum resultado vira master.

## Limitações conhecidas

- **Mão tatuada:** em qualquer denoise a mão vira "massa" ou textura de pelo (testes de transferência 1 a 7 e retoque). Ainda sem solução boa.
- **Anatomia:** sem detector automático de mãos/dedos.
- **Argola/maquiagem:** vêm da referência principal da Luna (ela usa argola grande e maquiagem forte). Melhorar trocando a referência, não o pipeline.
- **Lustify:** indisponível até o usuário fornecer o token do Civitai.
- **InsightFace (antelopev2):** licença não comercial (ver `docs/MODEL_REGISTRY.md`).
