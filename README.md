# Luna AI Studio

Aplicação web modular para geração e edição de imagens via ComfyUI, com suporte planejado a personas com identidade visual consistente.

```
Frontend → Backend/API → LLM → Prompt estruturado → ComfyUI → Modelo → Imagem → Frontend
```

Este documento cobre a **Fase 1**: infraestrutura mínima ponta a ponta (prompt → backend → ComfyUI → FLUX.1 Kontext → imagem → frontend), sem personas, LoRA, IP-Adapter ou FaceID.

## Estrutura

```
/frontend      React + Vite + TypeScript (chat, geração, resultado, galeria)
/backend       FastAPI (routes, services, clients, workflow_manager, model_manager, config)
/workflows     Grafos do ComfyUI em formato de API, com placeholders (independentes do backend)
/models        Metadados dos modelos disponíveis (nunca os .safetensors)
/personas      Perfis de persona (ex.: luna/) — vazio até a Fase 2
/config        Configuração não-sensível versionada (modelo/workflow padrão)
/scripts       Utilitários (ex.: teste de conexão com o ComfyUI)
```

## Infraestrutura (RunPod)

- ComfyUI: `https://h7dsgu25e43vo1-8188.proxy.runpod.net`
- JupyterLab: `https://h7dsgu25e43vo1-8888.proxy.runpod.net`
- GPU: NVIDIA L4, 24 GB VRAM (~23,6 GB reais) · Storage: 40 GB em `/workspace`
- Modelo atual: FLUX.1 Kontext [dev] (fp8 scaled) + clip_l + t5xxl (fp8) + vae (`ae.safetensors`)
- **O Pod ID muda a cada migração da RunPod** (histórico: `sp8bay1sk6qs2o` → `4uisgq0k317va6` → `h7dsgu25e43vo1`, migrações automáticas por falta de GPU livre no host anterior). Sempre confirme o ID atual em [console.runpod.io/pods](https://console.runpod.io/pods) antes de rodar — o volume de 40 GB em `/workspace` é preservado entre migrações, só a URL muda.

## Como rodar

### 1. Configurar variáveis de ambiente

```bash
cp .env.example .env
# edite .env com a URL/porta atuais do pod, se tiverem mudado
```

### 2. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Abra `http://localhost:5173`. O frontend consome a API em `http://localhost:8000/api` (ajustável via `VITE_API_BASE_URL`).

### 4. Verificar conexão com o ComfyUI (opcional, recomendado antes de gerar)

```bash
python scripts/check_comfyui_connection.py
```

Esse script confere se o pod está acessível e se os nós usados pelo workflow de teste (`workflows/flux-kontext-txt2img.json`) existem na instância atual do ComfyUI — os nomes de nós podem variar entre versões/custom nodes, então essa validação é importante antes da primeira geração real.

### 5. Ligar o Ollama (chat) + backend dentro do pod RunPod

O template atual do pod não inicia o Ollama nem o backend automaticamente no boot. Depois de subir o pod e dar `git pull`, rode uma vez (via Jupyter/terminal do pod):

```bash
bash scripts/runpod_bootstrap.sh
```

Isso instala o `zstd` e o Ollama se ainda não estiverem presentes, baixa o modelo definido em `LLM_MODEL` (padrão `llama3.2:3b`), sobe o `ollama serve` e o backend FastAPI em segundo plano (logs em `/tmp/luna-logs/`). É seguro rodar de novo — ele reinicia o que já estiver de pé em vez de duplicar processos.

**Economia:** o modelo do Ollama (`.ollama-models/`) e o ambiente Python (`.venv-persist/`) ficam salvos dentro do próprio repo, que vive no Network Volume persistente — ou seja, só baixam/instalam na primeira vez. Num pod novo (migração, GPU diferente), o script reconhece que já existem e pula direto para religar os processos, economizando minutos de GPU ligada.

## Regras do projeto

- Nenhum modelo `.safetensors`, imagem grande, API key ou `.env` real entra no Git (ver `.gitignore`).
- Modelos e workflows são plugáveis: adicionar um novo workflow é criar um `.json` em `/workflows`; adicionar um modelo é uma entrada em `/models/registry.json`. Nenhum download automático de modelo.
- `/personas/luna/references` é apenas o destino de importação futura — as 100+ imagens originais permanecem no Drive/Storage e não são copiadas para o Git.
- Técnicas de identidade (LoRA, IP-Adapter, FaceID, embeddings) só serão avaliadas e implementadas na Fase 2, após análise técnica de compatibilidade com FLUX.1 Kontext e a GPU L4.

## Roteiro

- **Fase 1** (este momento): infraestrutura mínima ponta a ponta.
- **Fase 2**: importação das referências da Luna, Persona Manager, Identity Profile, avaliação e implementação do método de consistência visual.
