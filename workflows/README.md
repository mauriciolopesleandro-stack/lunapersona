# /workflows

Cada arquivo `.json` neste diretório é um workflow independente no formato:

```json
{
  "meta": { "id": "...", "title": "...", "description": "...", "compatible_models": [...], "required_params": [...], "optional_params": {...} },
  "graph": { ... grafo de nós no formato de API do ComfyUI ... }
}
```

O `graph` usa placeholders `"${NOME}"` que o Workflow Manager substitui em tempo de execução pelos valores enviados pelo backend (parâmetros do usuário + arquivos do modelo escolhido, vindos de `/models/registry.json`).

A aplicação nunca fica presa a um único workflow: novos arquivos podem ser adicionados aqui sem tocar no núcleo do backend.

## Workflows atuais

- `chroma-txt2img.json` — texto → imagem com Chroma1-HD (fp8mixed), baseado no workflow oficial do modelo. Chroma não aceita imagem de referência: a identidade da persona vem só do texto do prompt. Os workflows FLUX Kontext (txt2img e reference) foram removidos na troca para o Chroma e continuam no histórico do Git.
