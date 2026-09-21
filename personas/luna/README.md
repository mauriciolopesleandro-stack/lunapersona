# Persona: Luna

Perfil de identidade da persona Luna (Fase 2).

- `persona.json` — perfil persistente: nome, descrição, Identity Profile (características **fixas** vs **variáveis**), modelo/workflow padrão e os métodos de preservação de identidade planejados/ativos. Versionado no Git (é configuração, não mídia).
- `references/` — imagens de referência enviadas pela interface (upload via `POST /api/personas/luna/references`). **Nunca versionadas no Git** (ver `.gitignore`) — ficam apenas no volume persistente do backend. `references/index.json` guarda os metadados de cada referência (id, arquivo, data de upload, se é a principal).

## Identity Profile: fixo vs variável

- **Fixo** (`identity.fixed`): características que NÃO devem mudar entre gerações — formato do rosto, olhos, sobrancelhas, nariz, boca, cabelo (formato/cor/textura), tom de pele, características corporais e outras características visuais permanentes.
- **Variável** (`identity.variable_defaults`): roupa, cenário, iluminação, pose, expressão, câmera — o que muda a cada geração.

## Mecanismo de identidade ativo hoje

Apenas **image prompting por texto**: ao gerar com `persona_id: "luna"`, o backend concatena as características fixas não vazias ao prompt do usuário antes de montar o workflow. Nenhuma alteração no grafo do ComfyUI.

## Ainda não implementado (proposital, fases futuras)

- IP-Adapter, FaceID, LoRA — a arquitetura (`identity_methods.planned`) já reserva o espaço, mas nada foi implementado.
- Thumbnails e deduplicação automática de referências.
- Treinamento de qualquer modelo.
