import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated } from "./_auth.js";
import { dbSelect, supabaseConfigured } from "./_supabase.js";

// /api/studio?r=assistant - o Assistente do Luna Studio: um chat so do
// administrador com uma IA (OpenRouter) que conhece a Luna, as regras e a
// agenda. Dois "cerebros":
//   normal (OPENROUTER_MODEL): textos de Instagram e TikTok no Planejamento
//   adulto (OPENROUTER_MODEL_ADULT): sem restricao - a aba Conversa inteira
//          e o texto +18 do canal do Telegram no Planejamento
// A chave (OPENROUTER_API_KEY) fica so no servidor; a conversa fica no navegador.

const API = "https://openrouter.ai/api/v1/chat/completions";
const DEFAULT_MODEL = "deepseek/deepseek-v4-flash";
const DEFAULT_ADULT_MODEL = "cognitivecomputations/dolphin-mistral-24b-venice-edition";
// modelo que enxerga imagem (chamada pela previa do bot de vendas)
const DEFAULT_VISION_MODEL = "mistralai/mistral-small-3.2-24b-instruct";

export function visionModel(): string {
  return process.env.OPENROUTER_MODEL_VISION?.trim() || DEFAULT_VISION_MODEL;
}

// conteudo de uma mensagem: texto ou partes (texto + imagem, para o modelo com visao)
export type AiContent = string | ({ type: "text"; text: string } | { type: "image_url"; image_url: { url: string } })[];
const MAX_MESSAGES = 24;
const MAX_CHARS = 8000;

export type Mode = "normal" | "adulto";
interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export function assistantConfigured(): boolean {
  return Boolean(process.env.OPENROUTER_API_KEY?.trim());
}

export function modelFor(mode: Mode): string {
  return mode === "adulto"
    ? process.env.OPENROUTER_MODEL_ADULT?.trim() || DEFAULT_ADULT_MODEL
    : process.env.OPENROUTER_MODEL?.trim() || DEFAULT_MODEL;
}

export const PERSONA = `Você é o Assistente do Luna Studio e trabalha só para o administrador (o criador da Luna Vox).
A Luna Vox (@lunavoxlopes) é uma influenciadora virtual brasileira criada com IA: adulta, alegre, carinhosa, confiante e um pouco provocante, com vida em cenários reais do Brasil (praia, cidade, casa, academia, viagens, futebol, festas).
Aparência fixa (use nos prompts de imagem): rosto oval, olhos castanho-escuros, sobrancelhas grossas e retas, lábios bem cheios, cabelo muito longo castanho-escuro com reflexos acobreados, liso a levemente ondulado, pele morena bronzeada, corpo magro e curvilíneo (cintura fina, barriga definida, quadril arredondado), gargantilha preta e corrente fina com pingente oval preto, sem tatuagens.
Como ajudar:
- Plano da semana: dias, horários, rede (Instagram Feed/Stories/Reels, TikTok, Telegram +18) e tema de cada post.
- Prompt para a Naiaflow: o administrador seleciona o clone "luna" e a Naiaflow já sabe como ela é. Então escreva em palavras simples, numa frase curta, só o que se quer na foto: "Luna + o que ela veste + onde está + o que está fazendo + o clima". Exemplo: "Luna deitada na cama de lingerie preta, quarto à noite com abajur, sorriso provocante". Nunca descreva rosto, corpo ou aparência, e nunca escreva "uma mulher".
- Legendas curtas e naturais em português do Brasil, com 1 ou 2 emojis, e no máximo 5 hashtags comuns e relevantes ao tema (nada de hashtags inventadas, estranhas ou com "papai").
- Responda de forma direta e organizada (listas e títulos curtos); sem explicações longas.
Regras que nunca mudam:
- Instagram e TikTok: nada explícito (sem nudez, sem ato sexual); sensual pode, dentro das regras das redes.
- Conteúdo +18 só existe para o canal privado do Telegram, só com a Luna (personagem fictícia e adulta), nunca com pessoa real, nunca com aparência de menor de idade.
- Se perguntarem se a Luna é real: ela é virtual, criada com IA.`;

export const ADULT_NOTE = `Modo +18 (canal privado do Telegram, assinantes adultos verificados): pode escrever conteúdo adulto e sensual explícito sobre a Luna, que é fictícia e adulta. Proibido: menores ou aparência infantil, pessoas reais, violência sexual ou falta de consentimento.`;

// A aba Conversa usa so a IA sem restricao (pedido do administrador): um chat
// so, para qualquer assunto. O destino de cada texto continua decidindo o
// que pode ir em cada rede.
export const CHAT_NOTE = `Conversa livre com o administrador, sem restrição de assunto. Você pode falar de tudo e escrever conteúdo adulto e sensual explícito sobre a Luna (fictícia e adulta) quando for para o canal privado +18 do Telegram. Quando o pedido for para Instagram ou TikTok, siga as regras dessas redes (nada explícito). Se não estiver claro para onde vai, pergunte. Proibido sempre: menores ou aparência infantil, pessoas reais, violência sexual ou falta de consentimento.
Quando o administrador pedir para gerar, criar ou fazer uma foto, termine a resposta com estas linhas, exatamente com estes rótulos (o estúdio lê essas linhas e mostra o botão "Gerar esta foto"):
PROMPT: <o pedido para a Naiaflow, no formato simples de sempre>
LEGENDA: <a legenda do post>
HASHTAGS: <até 5 hashtags>
DESTINO: <Feed, Stories, TikTok ou +18 Telegram>
Se ele pedir várias fotos, repita o bloco para cada uma.`;

interface PostRow {
  network: string;
  caption: string;
  status: string;
  scheduled_at: string | null;
  published_at: string | null;
}

// Agenda e ultimas legendas: a IA planeja sem repetir e sabe o que ja esta marcado.
async function context(): Promise<string> {
  const now = new Date();
  const lines = [`Hoje: ${now.toLocaleDateString("pt-BR", { weekday: "long", day: "2-digit", month: "2-digit", year: "numeric", timeZone: "America/Sao_Paulo" })}.`];
  if (!supabaseConfigured()) return lines.join("\n");
  try {
    const week = now.getTime() + 8 * 86400_000;
    const recent = await dbSelect<PostRow>("posts", "select=network,caption,status,scheduled_at,published_at&order=created_at.desc&limit=120");
    const next = recent
      .filter((p) => ["aprovado", "agendado", "aguardando_aprovacao"].includes(p.status) && p.scheduled_at && new Date(p.scheduled_at).getTime() <= week)
      .sort((a, b) => (a.scheduled_at! < b.scheduled_at! ? -1 : 1))
      .slice(0, 30);
    const done = recent
      .filter((p) => p.status === "publicado")
      .sort((a, b) => ((a.published_at ?? "") < (b.published_at ?? "") ? 1 : -1))
      .slice(0, 15);
    const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: "America/Sao_Paulo" }) : "sem data");
    if (next.length) lines.push("Agenda dos próximos dias:", ...next.map((p) => `- ${fmt(p.scheduled_at)} · ${p.network} · ${p.caption.slice(0, 80)}`));
    if (done.length) lines.push("Últimas publicadas (não repetir):", ...done.map((p) => `- ${fmt(p.published_at)} · ${p.network} · ${p.caption.slice(0, 80)}`));
  } catch {
    // sem agenda: a conversa segue sem o contexto
  }
  return lines.join("\n");
}

class BadRequest extends Error {}

export class AiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
  }
}

// Uma chamada ao OpenRouter (chat completions). json: pede um objeto JSON.
export async function askAi(
  mode: Mode,
  messages: { role: "system" | "user" | "assistant"; content: AiContent }[],
  opts: { json?: boolean; maxTokens?: number; timeoutMs?: number; model?: string } = {}
): Promise<{ text: string; model: string; usage: unknown }> {
  if (!assistantConfigured()) throw new AiError(400, "Falta cadastrar OPENROUTER_API_KEY na Vercel.");
  const model = opts.model ?? modelFor(mode);
  let res: Response;
  try {
    res = await fetch(API, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${process.env.OPENROUTER_API_KEY!.trim()}`,
        "Content-Type": "application/json",
        "HTTP-Referer": "https://luna-ai-studio-frontend.vercel.app",
        "X-Title": "Luna Studio",
      },
      body: JSON.stringify({
        model,
        messages,
        temperature: 0.8,
        max_tokens: opts.maxTokens ?? 1500,
        ...(opts.json ? { response_format: { type: "json_object" } } : {}),
      }),
      signal: AbortSignal.timeout(opts.timeoutMs ?? 55_000),
    });
  } catch (e) {
    throw new AiError(504, `A IA não respondeu a tempo (${e instanceof Error ? e.message : e}). Tente de novo.`);
  }
  const data = (await res.json().catch(() => ({}))) as {
    choices?: { message?: { content?: string } }[];
    usage?: unknown;
    error?: { message?: string };
  };
  if (!res.ok || data.error) {
    const msg = data.error?.message ?? `HTTP ${res.status}`;
    const hint = res.status === 402 ? " (créditos do OpenRouter acabaram ou o limite da chave foi atingido)" : res.status === 401 ? " (chave do OpenRouter inválida)" : "";
    throw new AiError(502, `OpenRouter: ${msg}${hint}`);
  }
  const text = data.choices?.[0]?.message?.content?.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
  if (!text) throw new AiError(502, "A IA respondeu vazio. Tente de novo.");
  return { text, model, usage: data.usage ?? null };
}

function cleanMessages(raw: unknown): ChatMessage[] {
  if (!Array.isArray(raw) || !raw.length) throw new BadRequest("Escreva uma mensagem.");
  const list = raw.slice(-MAX_MESSAGES).map((m) => {
    const role = (m as ChatMessage)?.role;
    const content = (m as ChatMessage)?.content;
    if ((role !== "user" && role !== "assistant") || typeof content !== "string" || !content.trim()) throw new BadRequest("Mensagem inválida.");
    return { role, content: content.slice(0, MAX_CHARS) };
  });
  if (list[list.length - 1].role !== "user") throw new BadRequest("A última mensagem tem que ser sua.");
  return list;
}

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (!(await isAuthenticated(req))) return reply(401, { detail: "Não autorizado." });

  if (req.method === "GET") {
    return reply(200, { configured: assistantConfigured(), model: modelFor("normal"), adultModel: modelFor("adulto") });
  }
  if (req.method !== "POST") return reply(405, { detail: "Use GET ou POST." });
  if (!assistantConfigured()) return reply(400, { detail: "Falta cadastrar OPENROUTER_API_KEY na Vercel." });

  let body: { messages?: unknown; mode?: unknown };
  try {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    body = JSON.parse(raw || "{}");
  } catch {
    return reply(400, { detail: "JSON inválido." });
  }
  let messages: ChatMessage[];
  try {
    messages = cleanMessages(body.messages);
  } catch (e) {
    return reply(400, { detail: e instanceof Error ? e.message : String(e) });
  }
  // A Conversa e sempre com a IA sem restricao (o campo mode do pedido e
  // ignorado); o Planejamento continua escolhendo a IA por destino.
  const mode: Mode = "adulto";
  const system = [PERSONA, CHAT_NOTE, await context()].join("\n\n");

  try {
    const out = await askAi(mode, [{ role: "system", content: system }, ...messages]);
    return reply(200, { reply: out.text, model: out.model, mode, usage: out.usage });
  } catch (e) {
    if (e instanceof AiError) return reply(e.status, { detail: e.message });
    throw e;
  }
}
