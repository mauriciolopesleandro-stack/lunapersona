# Estado atual do Luna AI Studio (passagem de sessão)

Atualizado em 2026-09-24, fim da sessão. Documento para o próximo Claude
continuar sem se perder. Leia inteiro antes de mexer em pod, GPU ou Vercel.

## 1. Visão rápida

- **Frontend**: React + Vite na Vercel — https://luna-ai-studio-frontend.vercel.app
  (projeto Vercel `lopesleandro/luna-ai-studio-frontend`, deploy automático a
  cada push na `main`). Funções serverless em `frontend/api/*.ts`.
- **Backend**: FastAPI (`backend/`), roda **dentro do pod RunPod**, junto com
  ComfyUI (imagem) e Ollama (chat).
- **Modelo de imagem**: Chroma1-HD fp8 (`models/registry.json`, workflow
  `workflows/chroma-txt2img.json`).
- **Chat**: `richardyoung/qwen3-14b-abliterated` no Ollama, em
  `/workspace/ollama_models`. Instalado pelo próprio usuário — **não instalar,
  baixar nem configurar modelo de LLM por conta própria.**
- **Persona**: só a Luna (`personas/luna/`). Foto principal atual:
  `luna_ref.png` (1 referência).

## 2. Como o estúdio liga hoje (failover, feito nesta sessão)

Antes: um pod fixo (`RUNPOD_POD_ID`) preso a uma máquina física; quando ela
lotava, o estúdio ficava dias sem ligar. Agora não há pod fixo.

`POST /api/runpod-wake` → `wakeStudio()` em `frontend/api/_runpod.ts`:

1. Se há pod `luna-studio-*` rodando, usa ele.
2. Senão, tenta religar um parado. Pula e marca para apagar os que estão num
   volume incompleto ou foram criados sem as variáveis atuais (S3 e
   `RUNPOD_API_KEY`).
3. Senão, cria um pod novo via REST (`POST rest.runpod.io/v1/pods`) com a GPU
   mais barata livre, até **US$ 0,60/h** (`LUNA_MAX_GPU_PRICE`), em ordem:
   A5000, L4, A40, 3090, A6000, RTX PRO 4000. Tenta os volumes nesta ordem:
   - `luna-models` — ID `1o5y5cpw99` — **US-MO-2** — original
   - `luna-models-ro` — ID `7s449owvmb` — **EU-RO-1** — cópia (US$ 2,80/mês)
4. Se nada deu certo, responde 503 "not enough free GPUs". A tela de login
   mostra "sem GPU" e tenta de novo a cada 5 min.

Detalhes importantes:
- **Imagem dos pods**: `runpod/comfyui:1.3.2-comfyuiv0.30.0-cuda12.8`. O pod
  antigo usava a variante cuda13.0, que roda em poucas máquinas. O venv do
  ComfyUI no volume usa o PyTorch da imagem, então as duas variantes servem.
- **Regra do usuário: nunca mais de um pod ligado na conta.** Há uma trava no
  Redis (`luna:runpod-wake-lock`), e o `/api/runpod-status` desliga pods extras
  a cada consulta, inclusive pods de teste criados à mão.
- Os endpoints `runpod-*` exigem login (cookie da sessão).
- O frontend não usa mais endereço fixo: pega `apiBase` e `backendReady` de
  `/api/runpod-status`. A tela espera até 10 min o backend responder.

### Autostart do pod

O entrypoint do pod baixa `scripts/pod_autostart.sh` da `main` do GitHub (repo
público) e roda antes do `/start.sh` da imagem. O autostart:

1. Faz clone ou pull do repositório.
2. Cria o venv `.venv-sync` e instala o boto3.
3. `volume_sync.py small`: `.env` e personas.
4. Se o volume é a cópia e ainda não tem o marcador de pronto: roda
   `volume_sync.py all` **antes** de subir o backend.
5. `runpod_bootstrap.sh`: Ollama + backend. O ID do pod e a `COMFYUI_URL` vêm
   do próprio pod, não do `.env`.
6. `volume_sync.py all`, e depois `small` a cada 5 min.

Logs no pod: `/tmp/luna-autostart.log` e `/tmp/luna-logs/*.log`.

### Sincronização dos volumes (`scripts/volume_sync.py`)

- Usa a API S3 da RunPod: o bucket é o ID do volume, o endpoint é
  `https://s3api-<dc>.runpod.io`. O outro volume não precisa ter pod ligado.
- Grupos:
  - `small`: `.env`, personas, `comfyui_args.txt`
  - `big`: `ollama_models`, `ComfyUI/models`, workflows salvos
  - Ficam de fora: venvs, código (vem do git), imagens geradas, custom nodes.
- A regra de cada arquivo fica em `/workspace/.luna-sync/state-<peer>.json`.
  Se um arquivo diferente se encontra nos dois lados pela primeira vez, **vence
  o volume original** (`LUNA_SELF_ORIGINAL=1`). Remoções em massa são puladas
  por segurança.
- Marcador de pronto: `.luna-sync/ready.json`, gravado nos dois volumes quando
  um `all` termina sem erro. **A cópia inicial já foi feita**: 38 GB em 5min44s,
  sem erros, e o marcador existe.
- O backend roda `volume_sync.py all` antes de se desligar por inatividade
  (`backend/app/idle_shutdown.py`).

### Variáveis na Vercel (Production)

- `RUNPOD_API_KEY`
- `RUNPOD_S3_ACCESS_KEY`, `RUNPOD_S3_SECRET_KEY` — criadas nesta sessão pelo
  usuário. O secret apareceu num print no chat e o usuário preferiu manter.
  Vale sugerir a troca.
- `RUNPOD_SSH_PRIVATE_KEY` — só para o `/api/runpod-bootstrap` manual.
- `SESSION_SECRET`, `APP_LOGIN_*`, `KV_*` (Redis Upstash)
- `RUNPOD_POD_ID` e `VITE_API_BASE_URL` — **legado**, podem ser removidas.
- Os pods recebem pela Vercel: `PUBLIC_KEY`, `RUNPOD_API_KEY` (para o
  auto-desligamento), as chaves S3 e `LUNA_SELF_*` / `LUNA_PEER_*`.

## 3. Situação no fim da sessão

- Último commit: `791746d` (na `main`, publicado na Vercel).
- Pod do estúdio: `luna-studio-eu-ro-1` (`bkgxfb3678ys0t`, L4, EU-RO-1). No fim
  da sessão o SSH recusava conexão, então **provavelmente já está parado.
  Confirme no console.** Esse pod foi criado **antes** da correção do
  auto-desligamento (não tem `RUNPOD_API_KEY` no ambiente). Na próxima vez que
  o estúdio ligar, ele é substituído por um pod novo, não religado.
- Saldo RunPod aproximado: US$ 8,5.
- Pods antigos parados (custam US$ 0 parados; apagar só se o usuário pedir):
  - `supreme_lime_cobra` (`nvecrkqq9wk66w`) — o pod fixo antigo
  - `allied_green_python` — RTX PRO 6000
  - `luna-teste-*` (3 pods de teste desta sessão)
  - vários `luna-comfyui*` / `luna-fastapi*` antigos

## 4. Pendências (em ordem)

1. **Testar o auto-desligamento**: ligar o estúdio (pod novo), deixar 8 min sem
   uso e confirmar que ele sincroniza e se desliga sozinho.
2. **Foto nova da Luna**: o usuário vai subir pela aba Personas → Luna →
   Referências e clicar em "Definir como principal" (botão criado nesta
   sessão). Conferir se o card da tela Gerar mostra a foto.
3. Testar a geração "Sem persona" (card novo na tela Gerar) e a geração com a
   Luna, ponta a ponta, no pod novo.
4. **Prompt da identidade** (recomendação dos testes, ainda não implementada):
   hoje o backend cola a identidade em português, longa e com anotações ("nas
   referências"...) na frente da cena. Os testes mostraram:
   - O texto em inglês e sem anotações dá identidade bem melhor.
   - Qualquer texto longo de rosto derruba a composição (vira retrato) e às
     vezes gera colagens.
   - Sugestão: identidade curta, em inglês, com a cena primeiro.
   - Script: `scripts/ab_prompt_test.py`.
5. **LoRA da Luna** (`C:\Users\mauri\Downloads\loras.zip`, arquivo
   `lunavox_sdxl_v1.safetensors`): é **SDXL** (base RealVisXL V5, gatilho
   `lunavox`), não funciona no Chroma. No teste, respeitou a composição e
   acertou a identidade em plano médio, mas o rosto deriva em close. Usar no
   estúdio exige um caminho SDXL novo (checkpoint + workflow). Não
   implementado; o usuário ainda não decidiu.
6. Segurança: o backend no pod não tem autenticação (quem souber a URL do proxy
   chama a API). Os endpoints da Vercel já exigem login.
7. Limpeza opcional: remover `RUNPOD_POD_ID` e `VITE_API_BASE_URL` da Vercel e
   apagar os pods antigos — só com o ok do usuário.

## 5. Preferências e regras do usuário

- Fala português, prefere explicação simples e passo a passo.
- **Nunca mais de um pod ligado.** Parar todo pod de teste ao terminar.
- Sempre visar o menor preço. Teto automático de US$ 0,60/h. Criar pod ou
  volume (compra) exige confirmar o preço antes. A RTX PRO 6000 (US$ 2,09/h)
  foi recusada para testes.
- Pode publicar direto na `main` depois de testar (a Vercel atualiza sozinha).
- Prefere que o Claude use o **navegador embutido** do app e peça login quando
  precisar.
- Senhas e chaves são digitadas pelo usuário, nunca pelo Claude.
- Não instalar nem baixar modelo de LLM (chat).

## 6. Como depurar rápido

- **Estúdio não liga**: veja o campo `attempts` na resposta do
  `/api/runpod-wake` (ou o console RunPod → Pods). Estoque por datacenter, sem
  chave:
  `POST https://api.runpod.io/graphql` com
  `gpuTypes(input:{id:"NVIDIA L4"}){lowestPrice(input:{gpuCount:1,dataCenterId:"US-MO-2"}){stockStatus}}`.
- **Pod ligado mas o site diz "Pod desligado"**: SSH no pod (chave em
  `.secrets/runpod_ssh_key`, porta no console → Connect → Direct TCP). Olhe
  `/tmp/luna-autostart.log` e `curl localhost:8000/api/health`.
- **Volumes diferentes**: rode no pod
  `/workspace/lunapersona/.venv-sync/bin/python scripts/volume_sync.py all`.
- Memória do Claude sobre isso: `runpod-failover-estudio`,
  `regra-um-pod-ligado`, `lora-lunavox-sdxl`.
