import { ADULT_NOTE, AiError, askAi, visionModel } from "./_assistant.js";

// +18 com foto de referencia (09/11): o PROMPT sai da IA que VE a foto (qwen VL),
// nao mais da IA de texto que so lia a descricao e inventava (pose "deitada de
// lado" numa foto em pe, "topoes"). A IA sem censura so escreve a legenda.
// Feed e Stories seguem com as duas IAs (leitura + texto), que vinham acertando.

const VISION_WRITER = `Você olha uma foto de referência e escreve o PROMPT para a Naiaflow recriar ESTA foto com a Luna (personagem fictícia e adulta; o rosto e o corpo dela vêm do clone "luna"). Conteúdo adulto do canal VIP pago: descreva a nudez e a cena sem suavizar.
Regras (da Naia, a IA da Naiaflow):
- Comece com "Luna". Uma ou duas frases, no máximo uns 350 caracteres: prompt longo faz o rosto da Luna mudar.
- Nunca descreva rosto, cabelo, olhos, pele, idade ou corpo, e nunca escreva "uma mulher", "jovem", "novinha".
- Diga a pose (em pé, sentada, deitada de costas, de bruços, de lado, de quatro…; de frente, de costas ou de lado para a câmera), para onde ela olha e onde estão braços e mãos.
- Diga o ângulo e a distância da câmera (de baixo para cima, de cima, na altura dos olhos, selfie, bem perto, corpo inteiro).
- Roupa EXATAMENTE como está: peça levantada, aberta ou afastada diga assim (ex.: "moletom creme levantado até o peito"); sem nada, "totalmente nua"; sem a parte de cima, "topless". Nunca acrescente peça que não está na foto.
- O lugar em poucas palavras (só o que dá para ver), a luz e o estilo da foto (celular, flash, selfie no espelho só se houver espelho).
- Se houver outra pessoa (ex.: um parceiro), diga onde está e o que aparece dela.
- Ignore marcas d'água, textos, ícones e números do print.
Responda só com o texto do prompt, sem rótulo, sem aspas e sem explicação.`;

const REFUSED = /n[ãa]o (posso|consigo|vou)|desculpe|I can(no|')t|I'm sorry|I am sorry|unable to/i;

// Prompt do +18 escrito por quem ve a foto. change: o que o administrador pediu
// para mudar (com o prompt anterior). Erro ou recusa: AiError (o pedido volta
// para o jeito antigo, com as duas IAs).
export async function visionPrompt(image: string, opts: { change?: string; previous?: string; fix?: string } = {}): Promise<string> {
  const ask = [
    VISION_WRITER,
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
  if (!/^luna\b/i.test(text) || (REFUSED.test(text.slice(0, 120)) && text.length < 400)) throw new AiError(502, "a IA com visão não escreveu o prompt");
  return text.slice(0, 900);
}

// Legenda do canal VIP para o prompt pronto (IA sem censura, so texto)
export async function vipCaption(prompt: string, change?: string, timeoutMs = 20_000): Promise<string> {
  const out = await askAi(
    "adulto",
    [
      {
        role: "system",
        content: `${ADULT_NOTE}\nVocê escreve a LEGENDA do canal VIP da Luna: uma historinha curta e bem safada, com a Luna falando em primeira pessoa com o assinante, de 3 a 5 frases, sobre aquele momento da foto (o que ela estava fazendo, o que sentiu, uma provocação ou convite para ver mais). Português do Brasil informal, pode ter emoji, sem hashtag. Responda só com a legenda, sem rótulo e sem aspas.`,
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
  return text
    .replace(/^\s*LEGENDA\s*[:：]\s*/i, "")
    .replace(/^["“]|["”]$/g, "")
    .replace(/\n\s*\n+/g, "\n")
    .trim()
    .slice(0, 1200);
}

// O bloco que o bot e o estudio leem (PROMPT/LEGENDA/DESTINO)
export const adultBlockText = (prompt: string, caption: string) => `PROMPT: ${prompt}\nLEGENDA: ${caption}\nDESTINO: +18 Telegram`;
