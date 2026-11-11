import { ADULT_NOTE, AiError, askAi, visionModel } from "./_assistant.js";
import { persona, personaIdentity, personaText } from "./_persona.js";

// jeito de falar da persona nas legendas (Luna: nada a mais, como sempre)
const voice = () => (personaIdentity() ? `\nQuem é ela e como fala (escreva na voz dela):\n${persona().brief}` : "");
import { missingGarments, nudityMismatch, readingRoupa } from "./_photo_blocks.js";

// Pedido com foto de referencia: o PROMPT sai da IA que VE a foto (qwen VL), nao
// mais da IA de texto que so lia a descricao e inventava a pose (09/11: "deitada
// de lado" numa foto em pe; 10/11: "de frente" numa foto sentada de lado).
// A IA de texto so escreve a legenda (e as hashtags no Feed/Stories).
// +18 desde 09/11; Feed e Stories desde 10/11 (a pedido, depois do +18 acertar).

export type WriterRoom = "18" | "feed";

function visionWriter(room: WriterRoom): string {
  const intro =
    room === "18"
      ? "Conteúdo adulto do canal VIP pago: descreva a nudez e a cena sem suavizar."
      : "Destino: Instagram e TikTok, nada explícito. Se a foto mostrar nudez (nua, topless, só de calcinha, seios à mostra), vista a Luna com a peça mais simples que combine com a cena (ex.: lingerie ou biquíni da mesma cor) e escreva a roupa já assim.";
  const nudez = room === "18" ? ' Sem nada, "totalmente nua"; sem a parte de cima, "topless".' : "";
  return `Você olha uma foto de referência e escreve o PROMPT para a Naiaflow recriar ESTA foto com a Luna (personagem fictícia e adulta; o rosto e o corpo dela vêm do clone "luna"). ${intro}
Regras (da Naia, a IA da Naiaflow):
- Comece com "Luna". Uma ou duas frases, no máximo uns 350 caracteres: prompt longo faz o rosto da Luna mudar.
- Nunca descreva rosto, cabelo, olhos, pele, idade ou corpo, e nunca escreva "uma mulher", "jovem", "novinha".
- Diga a pose (em pé, sentada, deitada de costas, de bruços, de lado, de quatro…), onde ela está apoiada (sobre a cama, no sofá, no pufe, no chão, na mesa…), para onde ela olha e onde estão braços, mãos e pernas.
- Diga para que lado o corpo está virado: de frente, de costas ou de lado para a câmera. Corpo de perfil com o rosto virado para a câmera é "de lado para a câmera, com o rosto virado para a câmera". Bumbum virado para a câmera é "de costas" (se o corpo estiver um pouco girado, "de costas, levemente de lado"); "de frente" só quando o peito está virado para a câmera.
- Diga a câmera: de onde ela olha (de frente, de trás, de lado), em que altura (de baixo para cima, na altura do quadril, do peito, dos olhos, de cima) e a distância (bem perto, meio corpo, corpo inteiro, selfie). Só escreva "na altura dos olhos" se for mesmo.
- Roupa EXATAMENTE como está: cite TODAS as peças que aparecem (em cima e embaixo: sutiã, top, blusa, vestido, saia, short, calcinha, meia…), cada uma com o nome certo, a cor e o tecido quando der para ver (cetim, renda, jeans). Peça levantada, aberta ou afastada diga assim (ex.: "moletom creme levantado até o peito", "saia azul-clara plissada levantada").${nudez} Descalça, salto ou tênis, se aparecer. Nunca acrescente peça que não está na foto.
- O lugar em poucas palavras (só o que dá para ver), a luz e o estilo da foto (celular, flash, selfie no espelho só se houver espelho).
- Se houver outra pessoa (ex.: um parceiro), diga onde está e o que aparece dela.
- Ignore marcas d'água, textos, ícones e números do print.
Responda só com o texto do prompt, sem rótulo, sem aspas e sem explicação.`;
}

const REFUSED = /n[ãa]o (posso|consigo|vou)|desculpe|I can(no|')t|I'm sorry|I am sorry|unable to/i;

// Prompt escrito por quem ve a foto. change: o que o administrador pediu para
// mudar (com o prompt anterior). Erro ou recusa: AiError (o pedido volta para o
// jeito antigo, com as duas IAs).
export async function visionPrompt(
  image: string,
  opts: { change?: string; previous?: string; fix?: string; roupa?: string; room?: WriterRoom } = {}
): Promise<string> {
  const ask = [
    personaText(visionWriter(opts.room ?? "18")),
    opts.roupa ? `Conferência: a leitura desta foto (feita antes) anotou a roupa assim: "${opts.roupa.slice(0, 300)}". Cite todas essas peças, com o mesmo nome, do jeito que estão na foto. Se a foto mostrar diferente, vale a foto.` : "",
    opts.previous ? `Prompt anterior: ${opts.previous}` : "",
    opts.change ? `Mudança pedida pelo administrador (vale mais que a foto nesse ponto): ${opts.change}` : "",
    opts.fix ?? "",
  ]
    .filter(Boolean)
    .join("\n\n");
  const out = await askAi("normal", [{ role: "user", content: [{ type: "text", text: ask }, { type: "image_url", image_url: { url: image } }] }], {
    model: visionModel(),
    maxTokens: 300,
    timeoutMs: 30_000,
    temperature: 0.2,
  });
  const text = out.text
    .replace(/^\s*(PROMPT\s*[:：]\s*)/i, "")
    .replace(/^["“]|["”]$/g, "")
    .replace(/\s+/g, " ")
    .trim();
  // o prompt comeca com o nome da persona ("Luna ...", "Cleuza ...")
  const starts = new RegExp(`^${persona().name}\\b`, "i");
  if (!starts.test(text) || (REFUSED.test(text.slice(0, 120)) && text.length < 400)) throw new AiError(502, "a IA com visão não escreveu o prompt");
  return text.slice(0, 900);
}

// O prompt com a conferencia da leitura (bot e estudio): a roupa da leitura vai
// junto; sem mudanca pedida, se o prompt esquecer/trocar peca que a leitura viu
// (ou, no +18, vestir quem esta nua), a IA olha a foto mais uma vez (uma so).
export async function checkedVisionPrompt(
  image: string,
  reading: string,
  opts: { change?: string; previous?: string; room?: WriterRoom } = {},
  budgetMs = 25_000
): Promise<string> {
  const started = Date.now();
  const room = opts.room ?? "18";
  const roupa = readingRoupa(reading) || undefined;
  const prompt = await visionPrompt(image, { ...opts, roupa, room });
  if (opts.change || !reading) return prompt;
  const notes: string[] = [];
  // no Feed/Stories a Luna vai vestida de proposito quando a foto esta nua
  const wrong = room === "18" ? nudityMismatch(reading, prompt) : null;
  if (wrong) notes.push(`a leitura diz "${wrong.nudity === "nua" ? "totalmente nua" : "topless"}" e o seu prompt tem "${wrong.word}"`);
  const missing = missingGarments(reading, prompt);
  if (missing.length) notes.push(`a leitura viu ${missing.join(", ")} e o seu prompt não cita`);
  if (!notes.length || Date.now() - started > budgetMs) return prompt;
  const fix = `Atenção, seu prompt anterior foi: ${prompt}\nProblema: ${notes.join("; ")}. Olhe a foto de novo e reescreva: peça que está na foto entra com o nome certo e do jeito que está (levantada, aberta, afastada); peça que não está, sai.`;
  return visionPrompt(image, { fix, roupa, room }).catch(() => prompt);
}

// erros de portugues que a IA de texto repete
const fixPt = (s: string) =>
  s
    .replace(/\b([Ii])magin([ae]) mim\b/g, (_m, i: string, v: string) => `${i === "I" ? "Me" : "me"} imagin${v}`)
    .replace(/\b([Ee])scondei\b/g, "$1scondi");

// Legenda do canal VIP para o prompt pronto (IA sem censura, so texto)
export async function vipCaption(prompt: string, change?: string, timeoutMs = 20_000): Promise<string> {
  const out = await askAi(
    "adulto",
    [
      {
        role: "system",
        content: personaText(`${ADULT_NOTE}\nVocê escreve a LEGENDA do canal VIP da Luna: uma historinha curta e bem safada, com a Luna falando em primeira pessoa com o assinante, de 3 a 5 frases, sobre aquele momento da foto (o que ela estava fazendo, o que sentiu, uma provocação ou convite para ver mais). Português do Brasil informal mas correto (ex.: "me imagina assim", nunca "imagina mim assim"; verbo no passado certo: "escondi", "senti", "vivi", nunca "escondei"), pode ter emoji, sem hashtag. Responda só com a legenda, sem rótulo e sem aspas.`) + voice(),
      },
      { role: "user", content: `A foto: ${prompt}${change ? `\n\nO administrador pediu: ${change}` : ""}` },
    ],
    { timeoutMs, maxTokens: 400 }
  );
  // so a legenda: se a IA escreveu um bloco inteiro, fica o texto do LEGENDA e
  // sai qualquer outro rotulo (um DESTINO perdido mandava o post para o Feed)
  let text = out.text;
  const leg = text.match(/LEGENDA\s*[:：]\s*([\s\S]*?)(?=\b(?:PROMPT|HASHTAGS|DESTINO)\s*[:：]|$)/i);
  if (leg) text = leg[1];
  text = text.replace(/\b(?:PROMPT|HASHTAGS|DESTINO)\s*[:：][^\n]*/gi, "");
  // sem linha em branco no meio (o bloco termina na linha em branco)
  return fixPt(
    text
      .replace(/^\s*LEGENDA\s*[:：]\s*/i, "")
      .replace(/^["“]|["”]$/g, "")
      .replace(/\n\s*\n+/g, "\n")
  )
    .trim()
    .slice(0, 1200);
}

// Legenda e hashtags do Feed/Stories para o prompt pronto (IA de texto)
export async function feedCaption(prompt: string, change?: string, timeoutMs = 20_000): Promise<{ caption: string; hashtags: string }> {
  const out = await askAi(
    "adulto",
    [
      {
        role: "system",
        content:
          personaText(`Você escreve a legenda de um post do Instagram/TikTok da Luna Vox, influenciadora virtual brasileira (nada explícito).
LEGENDA: uma frase curta, em tom de conversa ou pergunta provocante/fofa, com 1 a 3 emojis, sobre aquele momento da foto. Português do Brasil informal mas correto.
HASHTAGS: 2 a 4 curtas e comuns (#fy #praia #verão #explore…).
Responda exatamente assim, em duas linhas:
LEGENDA: …
HASHTAGS: #… #…`) + voice(),
      },
      { role: "user", content: `A foto: ${prompt}${change ? `\n\nO administrador pediu: ${change}` : ""}` },
    ],
    { timeoutMs, maxTokens: 200 }
  );
  const text = out.text.replace(/\*\*/g, "");
  const tagsLine = text.match(/HASHTAGS\s*[:：]\s*([^\n]*)/i)?.[1] ?? "";
  const hashtags = (tagsLine.match(/#[\p{L}\p{N}_]+/gu) ?? text.match(/#[\p{L}\p{N}_]+/gu) ?? []).slice(0, 4).join(" ");
  let caption = text.match(/LEGENDA\s*[:：]\s*([^\n]*)/i)?.[1] ?? text.split("\n").find((l) => l.trim() && !/^\s*(#|PROMPT|HASHTAGS|DESTINO)/i.test(l)) ?? "";
  caption = fixPt(caption.replace(/#[\p{L}\p{N}_]+/gu, "").replace(/^["“]|["”]$/g, "").trim()).slice(0, 300);
  return { caption, hashtags };
}

// O bloco que o bot e o estudio leem
export const adultBlockText = (prompt: string, caption: string) => `PROMPT: ${prompt}\nLEGENDA: ${caption}\nDESTINO: +18 Telegram`;
export const feedBlockText = (prompt: string, caption: string, hashtags: string, dest: "Feed" | "Stories") =>
  `PROMPT: ${prompt}\nLEGENDA: ${caption}\nHASHTAGS: ${hashtags}\nDESTINO: ${dest}`;
