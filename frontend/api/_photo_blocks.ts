// Blocos "PROMPT / LEGENDA / HASHTAGS / DESTINO" que a IA escreve no fim da
// resposta (ver CHAT_NOTE em _assistant.ts). Lidos no estudio (botao "Gerar
// esta foto") e no bot do Telegram (pedido mandado pelo celular). Sem
// dependencias: o navegador importa este arquivo.

export type Slot = "feed" | "stories" | "tiktok" | "telegram18";

export interface PhotoBlock {
  prompt: string;
  caption: string;
  hashtags: string;
  slot: Slot;
}

function slotFrom(text: string): Slot {
  const t = text.toLowerCase();
  if (t.includes("18") || t.includes("telegram")) return "telegram18";
  if (t.includes("stor")) return "stories";
  if (t.includes("tiktok")) return "tiktok";
  return "feed";
}

// Le os blocos "PROMPT: ... LEGENDA: ... HASHTAGS: ... DESTINO: ..." da resposta.
// Aceita o jeito que a IA escreve na pratica: numerado ("1. PROMPT:"), em
// negrito, com titulo ("### Prompt"), com nota ("PROMPT (Naiaflow):") e com o
// texto na linha de baixo do rotulo.
const LABEL = /^(PROMPT|LEGENDA|HASHTAGS|DESTINO)\b\s*(?:\([^)]*\))?\s*[:：\-–]?\s*(.*)$/i;

export function parsePhotoBlocks(text: string): PhotoBlock[] {
  const blocks: PhotoBlock[] = [];
  let cur: PhotoBlock | null = null;
  let sawSlot = false;
  let open: "prompt" | "caption" | "hashtags" | null = null; // rotulo sem texto: o texto vem nas linhas de baixo
  for (const raw of text.split("\n")) {
    // tira numeracao, marcadores e titulo markdown ("### "), mas nao o # da hashtag
    const line = raw.replace(/\*\*|__|`/g, "").replace(/^(?:#+\s+|[^\p{L}#])+/u, "").trim();
    const m = line.match(LABEL);
    if (!m) {
      // continuacao de um rotulo que veio vazio (para na linha em branco); a
      // legenda guarda as quebras de linha (historinha do canal +18)
      if (open && cur && line) cur[open] = cur[open] ? `${cur[open]}${open === "caption" ? "\n" : " "}${line}` : line;
      // (linha em branco logo depois do rotulo vazio nao fecha: o texto vem depois)
      else if (!line && (!open || !cur || cur[open])) open = null;
      continue;
    }
    const label = m[1].toUpperCase();
    let value = m[2].replace(/^["“]|["”]$/g, "").trim();
    // "PROMPT para a Foto 1 (Feed - Carrossel):" e titulo, nao o prompt: o texto
    // vem na linha de baixo (31/10 saiu uma foto com esse titulo de prompt)
    if (/[:：]$/.test(value)) value = "";
    open = null;
    if (label === "PROMPT") {
      // PROMPT logo depois de outro PROMPT (sem legenda no meio) = outra pose
      // do mesmo post: o carrossel o robo faz sozinho, vale o primeiro (a base)
      if (cur && cur.prompt && !cur.caption && !cur.hashtags && !sawSlot) continue;
      cur = { prompt: value, caption: "", hashtags: "", slot: "feed" };
      sawSlot = false;
      blocks.push(cur);
      if (!value) open = "prompt";
    } else if (cur) {
      if (label === "LEGENDA") {
        cur.caption = value;
        // a legenda pode continuar nas linhas de baixo (historinha de varias frases)
        open = "caption";
      } else if (label === "HASHTAGS") {
        cur.hashtags = value;
        if (!value) open = "hashtags";
      } else {
        cur.slot = slotFrom(value);
        sawSlot = true;
      }
    }
  }
  // aspas em volta do texto que veio na linha de baixo do rotulo
  const unquote = (s: string) => s.replace(/^["“]\s*|\s*["”]$/g, "").trim();
  return blocks.filter((b) => b.prompt).map((b) => ({ ...b, prompt: unquote(b.prompt), caption: unquote(b.caption) }));
}

// destinos do +18: o post no canal VIP e o conteudo dos pacotes do bot de vendas
export const ADULT_DESTS = [
  { key: "canal", label: "Canal VIP" },
  { key: "ensaio", label: "Pacote Ensaio" },
  { key: "album-vip", label: "Pacote Álbum VIP" },
];

// foto de referencia: a foto sai IGUAL (31/10). A Naiaflow segue mais o texto do
// que a IMG1, entao o prompt repete pose, roupa e cenario da leitura
export const REF_ASK =
  "Quero a foto IGUAL a esta referência, só que com a Luna (nunca a pessoa da foto). Ela vai como IMG1 no Criar da Naiaflow, mas escreva o PROMPT descrevendo tudo o que está na leitura, sem trocar nada: a mesma pose e o mesmo ângulo (de frente/de costas/de lado, para onde olha, braços e mãos), cada peça e acessório com o tipo exato, o mesmo cenário objeto por objeto, a luz e o estilo da foto. Nada de ícones ou números do print no prompt.";
