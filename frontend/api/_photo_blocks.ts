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

// creditos do carrossel: cada Criar da Naiaflow entrega 3 fotos da mesma cena
// (1 credito); passando de 3, o robo faz outro Criar com a mesma referencia e
// a mesma roupa e lugar, mudando a pose (6 fotos = 2 creditos, 9 = 3)
export const creditsFor = (photos: number) => Math.max(1, Math.ceil(photos / 3));
// +18: o post do canal sai com 3, 6 ou 9 fotos da mesma cena e uma historinha so
export const ADULT_PHOTOS = [3, 6, 9];

// destinos do +18: o post no canal VIP e o conteudo dos pacotes do bot de vendas
export const ADULT_DESTS = [
  { key: "canal", label: "Canal VIP" },
  { key: "ensaio", label: "Pacote Ensaio" },
  { key: "album-vip", label: "Pacote Álbum VIP" },
];

// foto de referencia: a foto sai IGUAL. 07/11 (Naia): prompt curto, so o que a
// IMG1 (com o rosto borrado) nao garante; texto longo tira forca do clone
export const REF_ASK =
  "Quero a foto IGUAL a esta referência, só que com a Luna (nunca a pessoa da foto). Ela vai como IMG1 no Criar da Naiaflow e já mostra pose, roupa e cenário: escreva um PROMPT CURTO (uma ou duas frases, no máximo uns 350 caracteres) com a pose e para onde ela olha (de frente/de costas/de lado), a roupa em poucas palavras (tipo e cor), o lugar em poucas palavras, a luz e o estilo da foto. Não descreva objeto por objeto nem peça por peça: prompt longo faz o rosto da Luna mudar. Nada de ícones ou números do print no prompt." +
  " Roupa do jeito que está na leitura: se ela estiver nua escreva \"totalmente nua\", sem a parte de cima \"topless\", só de calcinha \"só de calcinha\"; nunca acrescente lingerie, calcinha, sutiã, toalha ou peça que não esteja na leitura.";

// A foto que chega do Reference Bot traz uma legenda propria ("📌 FOTO DE
// REFERÊNCIA…" + uma descricao de outra IA). Ela nao e recado do administrador:
// vira so uma dica, e a leitura do estudio e que vale (07/11).
const REF_BOT_HEADER = /^\s*📌\s*FOTO DE REFER[ÊE]NCIA[^\n]*\n?/i;
export function splitRefBotNote(note: string): { ask: string; hint: string } {
  if (!REF_BOT_HEADER.test(note)) return { ask: note.trim(), hint: "" };
  return { ask: "", hint: note.replace(REF_BOT_HEADER, "").trim().slice(0, 800) };
}
export const refBotHintText = (hint: string) =>
  hint ? `\n\n[Dica do Reference Bot (outra IA, pode errar): se brigar com a leitura acima, vale a leitura]\n${hint}` : "";
