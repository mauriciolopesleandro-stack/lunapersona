# Persona Engine

Camada de persona independente do modelo: guarda a persona, gera pelo
provider escolhido, **confere se a imagem é a mesma pessoa** e, se não for,
ajusta e gera de novo (com teto de tentativas). Implementado em 2026-10-05.

```
PERSONA ─► GenerationRequest ─► PromptBuilder ─► ModelAdapter ─► imagem
                                                                   │
        ACEITA ◄─ sim ─ QualityGate ◄─ IdentityValidator ◄─────────┘
                         │ não
                         ▼
        FailureAnalyzer ─► RetryManager ─► gera de novo (até max_attempts, teto 8)
```

## Onde está cada coisa

| Peça | Arquivo |
|---|---|
| PersonaProfile (identidade / aparência / estilo / restrições, versões) | `backend/app/core/persona/profile.py`, `repository.py` |
| ReferenceManager (PRIMARY, FACE, FULL_BODY, PROFILE, STYLE, OTHER; peso; ativa; histórico) | `backend/app/core/persona/references.py` |
| GenerationRequest, PromptBuilder, orquestrador, RetryManager, histórico | `backend/app/core/generation/` |
| IdentityValidator, ScoreEngine, QualityGate, FailureAnalyzer, config | `backend/app/core/validation/` |
| Interface ModelAdapter + registro | `backend/app/providers/base.py` |
| Adapter do ComfyUI (Z-Image + LoRA, retoque InstantID) | `backend/app/providers/comfyui/adapter.py` |
| Medidas no pod (LunaFaces/InsightFace, Florence-2) | `backend/app/validation_backends/comfyui.py` |
| Rotas `/api/engine/...` | `backend/app/routes/persona_engine.py` |
| Logs estruturados | `backend/app/core/observability.py` (logger `luna.engine`) |
| Telas | aba Personas: "Aparência, estilo e regras", "Gerar com validação", "Histórico"; tipo/peso nas Referências |

**Regra:** nada em `app/core` importa ComfyUI, clientes ou serviços — só
`app/providers/base.py`. O teste `test_core_is_provider_agnostic` garante isso.
Provider novo = pasta nova em `app/providers/` + `register(...)` em `app/main.py`.

## Armazenamento (sem banco)

- Perfil: no próprio `personas/<id>/persona.json`. Os traços continuam em
  `identity.fixed` e roupa/expressão/luz/câmera em `identity.variable_defaults`
  (o pipeline antigo lê de lá); o resto fica no bloco `"engine"`.
  Cada alteração sobe a versão e grava `personas/<id>/engine/versions/vN.json`.
- Remover persona = desativar (`engine.active=false`). Remover foto = mover para
  `references/removed/` (histórico em `references/history.json`).
- Gerações: `personas/<id>/engine/jobs/<job>.json` com `results` (uma por
  tentativa), `failures` (com `result_id`) e `retries`. Tudo dentro de
  `personas/`, então o `volume_sync.py` já copia entre os volumes.
- Sem RLS (é uma conta só). A proteção é o token do backend (abaixo).

## Token do backend

O backend do pod recusa (401) toda alteração (POST/PUT/PATCH/DELETE) e todo
`/api/engine` sem o cabeçalho `X-Luna-Token`. O valor é derivado do
`SESSION_SECRET` na Vercel (`backendApiToken()` em `frontend/api/_auth.ts`),
vai para o pod como `LUNA_API_TOKEN` quando o pod é criado e para o site só
depois do login (`/api/runpod-status`). GET de fotos/áudios continua aberto
(`<img>` não manda cabeçalho). Pod sem a variável = backend aberto como antes;
pods parados sem o token são trocados por um novo na próxima vez que o
estúdio liga.

## Nota de identidade

Pesos em `config/identity_validation.json` (padrão da especificação):
rosto 40%, estrutura 20%, cabelo 10%, corpo 10%, idade 10%, traços 10%.

- **Rosto**: cosseno do ArcFace (LunaFaces) contra até 3 fotos ativas de
  rosto (principal, FACE, PROFILE); vale a mais parecida. Vira 0–1 pela
  calibragem `similarity_floor` (0.15) → `similarity_ceiling` (0.60).
- **Estrutura**: proporções pelos 5 pontos do rosto (olhos, nariz, boca,
  terço inferior), só com os dois rostos de frente. Precisa do LunaFaces novo
  (devolve `kps`); o ComfyUI só carrega a mudança ao reiniciar.
- **Idade**: InsightFace contra a idade aparente da persona (ou a da foto).
- **Traços marcantes**: palavras-chave da persona na descrição do Florence-2
  (medida fraca).
- **Cabelo e corpo**: sem modelo no pod → aparecem como "não medido"; o peso
  deles sai da conta e a parte medida aparece como `coverage`.

QualityGate: aceita só sem falha grave (sem rosto, sexo trocado), com
`coverage ≥ 0.5`, rosto ≥ 50% e nota ≥ limiar (**nota igual ao limiar
aceita**). Limiar: pedido > persona > provider > padrão (0.90).

## Pendências

1. **Calibrar no pod** (custa GPU): gerar ~10 fotos boas e ~10 ruins da Luna e
   ajustar `similarity_ceiling` e o limiar padrão. Com 0.60/0.90, uma foto
   precisa de ArcFace ≈ 0.55+ para passar; a LoRA do Z-Image costumava dar
   ~0.5. Sem calibrar, espere muitas recusas.
2. Marcar o tipo das fotos da Luna (aba Referências): as antigas entram como
   "Outra" e só a principal conta na validação até alguém marcar "Rosto"/"Perfil".
3. Cabelo e corpo: precisam de um modelo de segmentação/pose no pod.

## Testes

```
cd backend
.venv\Scripts\python -m pytest
```

Sem GPU nem pod: o modelo e o detector de rosto são falsos nos testes
(`tests/fakes.py`). O venv local é criado com
`python -m venv backend/.venv` + `pip install -r backend/requirements-dev.txt`.
