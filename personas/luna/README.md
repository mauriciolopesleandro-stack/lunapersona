# Persona: Luna

Este diretório vai conter, na **Fase 2**, o perfil de identidade da persona Luna.

- `references/` — pasta de destino para importação das 100+ imagens de referência (hoje no Drive). As imagens originais **nunca são movidas nem modificadas**; nada aqui é versionado no Git (ver `.gitignore`). Nada foi importado ainda.

## Ainda não implementado (Fase 2)

- Persona Manager (carrega o perfil da Luna e expõe ao backend)
- Identity Profile (estrutura de dados que descreve a identidade visual)
- Importação/organização das referências, thumbnails, deduplicação
- Avaliação técnica do método de preservação de identidade (LoRA / IP-Adapter / FaceID / embeddings) compatível com FLUX.1 Kontext e a GPU L4 24GB — **nenhuma dessas técnicas foi implementada ainda**, propositalmente.
