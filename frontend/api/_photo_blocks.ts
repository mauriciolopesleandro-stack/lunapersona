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
  // 09/11: a IA as vezes escreve o bloco todo numa linha ("... PROMPT: x LEGENDA: y
  // DESTINO: z"); cada rotulo em MAIUSCULAS no meio da linha vira uma linha nova
  const lines = text.replace(/[ \t]+(?:\*\*)?(PROMPT|LEGENDA|HASHTAGS|DESTINO)(?:\*\*)?\s*[:：]/g, "\n$1:").split("\n");
  for (const raw of lines) {
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

// 09/11: o pedido as vezes misturava lugar ou roupa de fotos anteriores. Vai no
// comeco de todo pedido com foto de referencia (bot e estudio).
export const REF_FRESH =
  "Esta é uma foto NOVA. Use só a leitura abaixo. Ignore qualquer foto, lugar ou roupa de pedidos anteriores. Não troque nada para variar: aqui o objetivo é ficar IGUAL.";

export type Nudity = "nua" | "topless" | null;

const clean = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

// a nudez que a leitura descreve (na linha da roupa, se houver; senao no texto todo)
export function readingNudity(reading: string): Nudity {
  const lines = reading.split("\n");
  const roupa = lines.filter((l) => /roupa|nua|topless|calcinha/i.test(l)).join(" ") || reading;
  const t = clean(roupa).replace(/(nao|nem) (esta |aparece )?(totalmente |completamente )?nua/g, "");
  if (/totalmente nua|completamente nua|sem (nenhuma )?roupa|\bnua\b|\bnuda\b/.test(t) && !/so de calcinha|apenas (de |com )?calcinha|so com calcinha/.test(t)) return "nua";
  // so de calcinha: em cima nao tem nada (sutia ou top no prompt e erro)
  if (/topless|sem a parte de cima|seios? (a mostra|de fora|nus)|sem sutia|sem top\b|so de calcinha|apenas (de |com )?calcinha|so com calcinha/.test(t)) return "topless";
  return null;
}

const FULL_COVER = /\b(calcinha|sutia|lingerie|biquini|maio|body|vestido|camisola|short|shortinho|saia|blusa|camiseta|cropped|top|roupao|robe|pijama|meia-calca|fio dental)\b|(enrolada|coberta|cobrindo|segurando)[^.,;]{0,25}(toalha|lencol)/;
const TOP_COVER = /\b(sutia|top|cropped|blusa|camiseta|vestido|body|maio|camisola|roupao|robe|pijama)\b|(?<!(calcinha|parte de baixo) d[eo] )\bbiquini\b(?! (so )?(a )?parte de baixo)|(enrolada|coberta|cobrindo)[^.,;]{0,25}(toalha|lencol)/;

// Prompt que veste quem esta nua na foto (+18): devolve a peca que sobrou
export function nudityMismatch(reading: string, prompt: string): { nudity: Exclude<Nudity, null>; word: string } | null {
  const nudity = readingNudity(reading);
  if (!nudity) return null;
  // "totalmente nua, sem nenhuma peca de roupa" nao conta como roupa
  const p = clean(prompt).replace(/sem (nenhuma |qualquer )?(peca (de roupa)?|roupa|calcinha|sutia|lingerie|top)( nenhuma)?/g, "");
  const hit = p.match(nudity === "nua" ? FULL_COVER : TOP_COVER);
  return hit ? { nudity, word: hit[0].trim() } : null;
}

const readingLines = (reading: string) =>
  reading
    .split("\n")
    .map((l) => l.replace(/^[\s>*#\-•\d.)]+/, "").replace(/\*\*/g, "").trim())
    .filter(Boolean);
const isRoupaLine = (l: string) => /roupa|nua|topless|calcinha|biqu|vestid/i.test(l);

// a linha da roupa da leitura ("" se nao houver)
export const readingRoupa = (reading: string): string => readingLines(reading).find(isRoupaLine) ?? "";

// 10/11: a IA que ve a foto escreveu "short" numa saia e esqueceu o sutia que a
// leitura tinha visto. Cada grupo e uma peca (com os nomes que valem por ela).
const GARMENTS: [string, RegExp][] = [
  ["saia", /\bsaia\b/],
  ["short", /\bshort(inho)?s?\b/],
  ["calça", /\bcalca\b|\bjeans\b|\blegging\b/],
  ["sutiã", /\bsutia\b|\bbralette\b/],
  ["calcinha", /\bcalcinha\b|\bfio dental\b|\btanga\b|\bthong\b/],
  ["vestido", /\bvestido\b/],
  ["moletom", /\bmoletom\b/],
  ["camiseta", /\bcamiseta\b|\bcamisa\b|\bblusa\b/],
  ["top", /\btop\b|\bcropped\b/],
  ["body", /\bbody\b/],
  ["biquíni", /\bbiquini\b/],
  ["meia", /\bmeias?\b|\bmeia-calca\b/],
  ["camisola", /\bcamisola\b|\bbaby ?doll\b/],
  ["roupão", /\broupao\b|\brobe\b/],
  ["cinta-liga", /\bcinta-?liga\b/],
  ["salto", /\bsalto\b/],
];

// pecas que a leitura viu e o prompt nao cita ("sem sutia" nao conta como peca)
export function missingGarments(reading: string, prompt: string): string[] {
  const roupa = clean(readingRoupa(reading)).replace(/\b(sem|nem|nao tem|nao usa)\b [^,.;]{0,30}/g, "");
  const p = clean(prompt);
  return GARMENTS.filter(([, re]) => re.test(roupa) && !re.test(p)).map(([name]) => name);
}

// uma linha da leitura para o resumo: roupa/nudez e lugar
export function readingGist(reading: string): string {
  const lines = readingLines(reading);
  const roupa = lines.find(isRoupaLine);
  const lugar = lines.find((l) => /lugar|cen[áa]rio/i.test(l) && l !== roupa);
  // qual IA leu a foto (o estudio marca no fim da leitura)
  const model = reading.match(/Leitura feita pelo modelo (\S+?)\)/)?.[1];
  return [roupa, lugar]
    .filter(Boolean)
    .map((l) => l!.slice(0, 160))
    .concat(model ? [`(${model})`] : [])
    .join(" · ");
}
