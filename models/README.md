# /models

Este diretório contém apenas **metadados** dos modelos usados pelo ComfyUI — nunca os arquivos `.safetensors` em si (esses ficam no volume persistente da RunPod, em `/workspace`, e nunca são versionados no Git).

- `registry.json` — lista de modelos conhecidos pelo sistema: id, engine, arquivos esperados (nomes, não conteúdo), parâmetros padrão e workflows compatíveis.

## Adicionando um novo modelo

1. Verifique a documentação, origem e licença do modelo antes de qualquer coisa.
2. Confirme compatibilidade com a GPU disponível (L4, 24 GB VRAM).
3. Faça o upload manual dos arquivos para `/workspace` no pod (fora do Git).
4. Adicione uma entrada em `registry.json` com os nomes exatos dos arquivos.
5. Não baixe nem instale modelos automaticamente — essa etapa é sempre manual e intencional.
