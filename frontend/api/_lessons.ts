import { AiError, askAi, visionModel } from "./_assistant.js";
import { storeConfigured, storeGet, storeSet } from "./_store.js";

// Aprendizado dos prompts: depois de cada foto que a Naiaflow gera, o robo do
// PC manda uma copia pequena (action "learn" em api/_worker.ts) e a IA com
// visao compara a foto com o pedido. A licao (o que saiu diferente e como
// escrever o proximo prompt) fica guardada e entra no contexto da Conversa,
// que passa a escrever prompts melhores. As correcoes pedidas no Telegram
// tambem viram licao. Nao aprova nem barra nada: quem aprova e o administrador.

const KEY = "luna:licoes-prompt";
const MAX_LESSONS = 25;
const DATA_URL = /^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/;
const MAX_IMAGE_CHARS = 1_500_000;

export interface Lesson {
  at: string;
  prompt: string;
  // de onde veio: a IA olhando a foto, ou o pedido de correcao do administrador
  source: "foto" | "correcao";
  ok?: boolean;
  lesson: string;
}

const LOOK_PROMPT = `Você ajuda a melhorar os prompts que geram as fotos da Luna (influenciadora virtual brasileira) na Naiaflow. Abaixo está o pedido (prompt) e, anexa, a foto que saiu.
Primeiro olhe a foto com atenção e descreva o que está nela de verdade; depois compare com o pedido. Responda em JSON, em português do Brasil:
{"roupa_vista": "o que a pessoa veste de verdade na parte de cima e na de baixo (diga 'sem nada em cima, seios à mostra' se for o caso)", "cenario_visto": "lugar e objetos principais que aparecem", "atendeu": true ou false,"diferencas": "o que saiu diferente do pedido ou com defeito (roupa a mais ou a menos, pose, cenário, enquadramento cortando cabeça ou pés, mãos, rosto, texto estranho); vazio se nada", "licao": "UMA regra curta e prática (até 2 frases) de como escrever o próximo prompt para acertar; vazio se o pedido saiu perfeito"}
Descreva só o que aparece de verdade. Não identifique pessoas reais.`;

async function load(): Promise<Lesson[]> {
  if (!storeConfigured()) return [];
  try {
    const list = JSON.parse((await storeGet(KEY)) ?? "[]");
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

async function remember(lesson: Lesson): Promise<void> {
  if (!storeConfigured() || !lesson.lesson.trim()) return;
  const list = [lesson, ...(await load())].slice(0, MAX_LESSONS);
  await storeSet(KEY, JSON.stringify(list));
}

// A IA olha a foto gerada e anota a licao. Devolve a licao (ou null se a foto
// saiu como pedido).
// modelo que olha as fotos para o aprendizado (OPENROUTER_MODEL_LESSONS troca sem deploy)
export function lessonsModel(): string {
  return process.env.OPENROUTER_MODEL_LESSONS?.trim() || visionModel();
}

// opts.model/opts.dry: so para comparar modelos (a licao nao e guardada)
export async function learnFromPhoto(prompt: string, image: unknown, opts: { model?: string; dry?: boolean } = {}): Promise<Lesson | null> {
  const img = String(image ?? "");
  if (!DATA_URL.test(img) || img.length > MAX_IMAGE_CHARS) throw new AiError(400, "Foto inválida ou grande demais para a análise.");
  const out = await askAi(
    "normal",
    [
      {
        role: "user",
        content: [
          { type: "text", text: `${LOOK_PROMPT}\n\nPedido: ${prompt.slice(0, 1500)}` },
          { type: "image_url", image_url: { url: img } },
        ],
      },
    ],
    { model: opts.model || lessonsModel(), json: true, maxTokens: 600, timeoutMs: 40_000 }
  );
  let parsed: { atendeu?: unknown; diferencas?: unknown; licao?: unknown };
  try {
    parsed = JSON.parse(out.text.replace(/^```(?:json)?|```$/g, "").trim());
  } catch {
    return null;
  }
  const diff = String(parsed.diferencas ?? "").trim();
  const rule = String(parsed.licao ?? "").trim();
  if (!rule) return null;
  const lesson: Lesson = {
    at: new Date().toISOString(),
    prompt: prompt.slice(0, 160),
    source: "foto",
    ok: parsed.atendeu === true,
    lesson: (diff ? `${diff} → ${rule}` : rule).slice(0, 400),
  };
  if (!opts.dry) await remember(lesson);
  return lesson;
}

// Correcao pedida pelo administrador no Telegram: o pedido ja e a licao.
export async function learnFromCorrection(prompt: string, text: string): Promise<void> {
  await remember({
    at: new Date().toISOString(),
    prompt: prompt.slice(0, 160),
    source: "correcao",
    lesson: `O administrador pediu para corrigir: "${text.trim().slice(0, 300)}"`,
  }).catch(() => undefined);
}

export async function listLessons(): Promise<Lesson[]> {
  return load();
}

export async function forgetLessons(): Promise<void> {
  if (storeConfigured()) await storeSet(KEY, "[]");
}

// Para o contexto da Conversa: as licoes mais recentes.
export async function lessonsForChat(limit = 12): Promise<string> {
  const list = (await load()).slice(0, limit);
  if (!list.length) return "";
  return [
    "Aprendizado das últimas fotos geradas (use para escrever prompts melhores para a Naiaflow; o mais recente primeiro):",
    ...list.map((l) => `- ${l.source === "correcao" ? "[correção]" : l.ok ? "[saiu bem]" : "[saiu diferente]"} pedido "${l.prompt.slice(0, 80)}…": ${l.lesson}`),
  ].join("\n");
}
