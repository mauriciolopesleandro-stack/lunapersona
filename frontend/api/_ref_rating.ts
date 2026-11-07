// Nota do video de referencia da dancinha (pedido de 03/11): antes de gastar
// credito no Copiar movimento, o robo do PC manda 6 quadros do video e a IA com
// visao (OPENROUTER_MODEL_VISION) preve de 0 a 100 como vai ficar, com as regras
// que a propria Naia passou (luz uniforme, corpo inteiro, camera parada, fundo
// simples, sem agua/fumaca/multidao). Nota boa: o robo segue e o administrador
// recebe a nota no Telegram. Nota baixa: nao gera; o pedido fica parado
// ("planejado") com os botoes "Gerar mesmo assim" e "Cancelar".
import { askAi, visionModel } from "./_assistant.js";
import { dbSelect, dbUpdateWhere, audit } from "./_supabase.js";
import { telegram, telegramConfig } from "./_telegram.js";

export const MIN_SCORE = 70;
const MAX_FRAMES = 8;

export const REF_PROMPT = `Você avalia vídeos de referência para o "Copiar movimento" da Naiaflow: a IA copia o movimento da pessoa do vídeo e troca pela personagem Luna (o rosto e o corpo vêm da Luna; a luz, as sombras, o cenário e a câmera vêm do vídeo).
Você recebe alguns quadros do vídeo, em ordem. Preveja de 0 a 100 o quanto o resultado vai ficar bom, pelas regras que a Naiaflow ensinou:
- Luz uniforme e difusa (dia nublado, sombra aberta, quarto bem iluminado) é ótima. Sol forte, faixas de sol e sombra no corpo, sombra de árvore/folhas, de grade ou de porta de vidro viram MANCHAS na pele: tire muitos pontos.
- Uma pessoa só, de corpo inteiro ou quase, sem partes cortadas. Mais gente no quadro, multidão ou pessoa pequena: tire pontos.
- Câmera parada ou com movimento suave; cortes, zoom e câmera tremida pioram.
- Fundo simples e parado. Água em movimento (mar batendo, cachoeira, piscina com a pessoa dentro), fumaça, fogo e reflexos/espelhos costumam sair estranhos: tire muitos pontos.
- Roupa colada ou simples; tecido muito solto, transparência e roupa branca estourada de luz distorcem. Cabelo voando forte e mãos muito rápidas perto do rosto também.
- Imagem escura, borrada ou de baixa resolução: tire pontos.
- Se a pessoa parecer menor de idade, nota 0.
Seja honesto e prático: 85-100 = deve ficar ótimo; 70-84 = bom, com algum risco; abaixo de 70 = provavelmente sai com defeito.
Responda só JSON: {"nota": <0-100>, "bom": ["até 3 pontos curtos"], "problemas": ["até 3 pontos curtos"], "dica": "uma frase curta do que trocar no vídeo"}`;

// 06/11: no "Trocar pessoa" (o padrao novo) a cena inteira vem do video e so a
// pessoa tocada vira a Luna. Regua que a Naia passou (soma 100) + quem aparece no
// 1o quadro, com a posicao, para ele escolher quem sai.
export const REF_PROMPT_TROCA = `Você avalia vídeos de referência para o "Trocar pessoa" da Naiaflow: o vídeo fica igual (cena, roupa, fundo, câmera, movimento) e só uma pessoa escolhida vira a personagem Luna.
Você recebe alguns quadros do vídeo, em ordem. Dê a nota de 0 a 100 somando os pontos desta régua (a da própria Naiaflow):
- Pessoa que vai virar a Luna clara e central (0-20): rosto visível o tempo todo, não fica de costas nem escondida.
- Movimento e pose (0-15): movimento suave e natural; muito rápido, muito perto da câmera ou girando o corpo tira pontos.
- Luz no rosto (0-15): rosto bem iluminado; rosto escuro, luz colorida forte ou piscando tira pontos.
- Enquadramento e câmera (0-15): câmera parada ou lenta, a pessoa ocupa de 30% a 70% da tela; cortes, zoom e câmera tremida tiram pontos.
- Fundo (0-10): o fundo é mantido, então só tira pontos se confundir com a pessoa (gente passando na frente, efeitos).
- Quantidade de pessoas (0-10): 10 com uma pessoa; 5 com duas bem separadas; 0 se a pessoa se sobrepõe a outras.
- Roupa e corpo (0-10): roupa justa ou com recortes claros; casaco largo ou objeto cobrindo o corpo tira pontos.
- Água, fumaça, reflexos (0-5).
- Corpo (07/11, a Naia confirmou): o Trocar pessoa troca só o rosto; o corpo, a altura e o biotipo vêm sempre do vídeo. A Luna é magra e curvilínea (cintura fina, barriga definida, quadril arredondado, estatura média). Se o corpo da pessoa for muito diferente disso (bem mais gorda ou bem mais magra, muito alta, ombros largos, corpo masculino), a nota final fica no máximo 50 e diga isso nos problemas; um pouco diferente, tire até 15 pontos. Roupa solta que disfarça o corpo ajuda.
- Se a pessoa parecer menor de idade, nota 0.
Liste também as pessoas que aparecem grandes no PRIMEIRO quadro (no máximo 4, a mais destacada primeiro), cada uma com uma descrição curta (roupa, cabelo, lado) e o ponto do meio do tronco dela em fração da largura (x) e da altura (y) do quadro, de 0 a 1.
Responda só JSON: {"nota": <0-100>, "bom": ["até 3 pontos curtos"], "problemas": ["até 3 pontos curtos"], "dica": "uma frase curta", "pessoas": [{"descricao": "mulher de vestido rosa, no meio", "x": 0.5, "y": 0.5}]}`;

export type RefMode = "trocar" | "copiar";

export interface RefPerson {
  desc: string;
  x: number;
  y: number;
}

export interface RefRating {
  score: number;
  good: string[];
  problems: string[];
  tip: string;
  people?: RefPerson[];
}

interface ItemRow {
  id: string;
  plan_id: string;
  status: string;
  scheduled_at: string;
  network: string;
  format: string;
}

const list = (v: unknown) => (Array.isArray(v) ? v.map((x) => String(x).trim()).filter(Boolean).slice(0, 3) : []);

export function parseRating(text: string): RefRating {
  const json = JSON.parse(text.replace(/^```(?:json)?\s*|\s*```$/g, "")) as Record<string, unknown>;
  const score = Math.round(Number(json.nota));
  if (!Number.isFinite(score)) throw new Error("a IA não deu a nota");
  const frac = (v: unknown) => Math.min(0.98, Math.max(0.02, Number(v)));
  const people = (Array.isArray(json.pessoas) ? json.pessoas : [])
    .map((p) => p as Record<string, unknown>)
    .filter((p) => Number.isFinite(Number(p.x)) && Number.isFinite(Number(p.y)))
    .slice(0, 4)
    .map((p) => ({ desc: String(p.descricao ?? "pessoa").trim().slice(0, 50), x: frac(p.x), y: frac(p.y) }));
  return { score: Math.min(100, Math.max(0, score)), good: list(json.bom), problems: list(json.problemas), tip: String(json.dica ?? "").trim().slice(0, 200), ...(people.length ? { people } : {}) };
}

export async function rateFrames(frames: string[], mode: RefMode = "trocar"): Promise<RefRating> {
  const images = frames.filter((f) => typeof f === "string" && f.startsWith("data:image/jpeg;base64,")).slice(0, MAX_FRAMES);
  if (images.length < 2) throw new Error("mande pelo menos 2 quadros do vídeo");
  const out = await askAi(
    "normal",
    [
      { role: "system", content: mode === "trocar" ? REF_PROMPT_TROCA : REF_PROMPT },
      {
        role: "user",
        content: [{ type: "text", text: `Quadros do vídeo de referência (${images.length}, do começo ao fim):` }, ...images.map((url) => ({ type: "image_url" as const, image_url: { url } }))],
      },
    ],
    { json: true, model: visionModel(), maxTokens: 700, timeoutMs: 45_000, temperature: 0.2 }
  );
  return parseRating(out.text);
}

const when = (iso: string) => new Date(iso).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

export function ratingText(r: RefRating): string {
  return [
    ...(r.good.length ? r.good.map((g) => `✅ ${g}`) : []),
    ...(r.problems.length ? r.problems.map((p) => `⚠️ ${p}`) : []),
    ...(r.tip ? [`💡 ${r.tip}`] : []),
  ].join("\n");
}

async function planRequest(item: ItemRow): Promise<Record<string, unknown> & { motion?: Record<string, unknown> }> {
  const [plan] = await dbSelect<{ request: (Record<string, unknown> & { motion?: Record<string, unknown> }) | null }>("content_plans", `select=request&id=eq.${item.plan_id}`);
  return plan?.request ?? {};
}

// Chamado pelo robo (acao rate-ref). Devolve { score, go }.
export async function rateReference(item: ItemRow, frames: unknown): Promise<RefRating & { go: boolean }> {
  const request = await planRequest(item);
  // pedido de antes de 06/11 (sem modo) e Copiar dancinha
  const rating = await rateFrames(Array.isArray(frames) ? (frames as string[]) : [], request.motion?.mode === "trocar" ? "trocar" : "copiar");
  const seconds = Number(request.motion?.seconds ?? 5);
  await dbUpdateWhere("content_plans", `id=eq.${item.plan_id}`, {
    request: { ...request, motion: { ...(request.motion ?? {}), rating: { score: rating.score, at: new Date().toISOString() } } },
  });
  const go = rating.score >= MIN_SCORE;
  const head = `🔎 Nota prevista da dancinha (${item.format} ${when(item.scheduled_at)}): ${rating.score}/100`;
  const config = await telegramConfig();
  if (config.token && config.chatId) {
    if (go) {
      await telegram(config.token, "sendMessage", { chat_id: config.chatId, text: `${head}\n${ratingText(rating)}\n\n▶️ Boa o bastante: o robô já está gerando (${seconds + 1} créditos).` }).catch(() => undefined);
    } else {
      await telegram(config.token, "sendMessage", {
        chat_id: config.chatId,
        text: `${head}\n${ratingText(rating)}\n\n⏸️ Abaixo de ${MIN_SCORE}: não gastei crédito. Quer gerar mesmo assim (${seconds + 1} créditos) ou cancelar e mandar outro vídeo?`,
        reply_markup: {
          inline_keyboard: [[{ text: `▶️ Gerar mesmo assim (${seconds + 1} créditos)`, callback_data: `rg:${item.id}` }], [{ text: "🗑️ Cancelar", callback_data: `rx:${item.id}` }]],
        },
      }).catch(() => undefined);
    }
  }
  if (!go) {
    // parado fora da fila ate ele decidir; o TikTok do mesmo video espera junto
    await dbUpdateWhere("content_plan_items", `id=eq.${item.id}`, { status: "planejado", attempts: 0, note: `Nota da referência ${rating.score}/100: esperando a decisão no Telegram`, updated_at: new Date().toISOString() });
  }
  await audit("nota_referencia", "content_plan_items", item.id, { nota: rating.score, gerar: go });
  return { ...rating, go };
}

// Botoes do Telegram: rg = gerar mesmo assim (o robo nao avalia de novo), rx = cancelar.
export async function refDecision(kind: "rg" | "rx", id: string): Promise<string> {
  if (!/^[0-9a-f-]{36}$/i.test(id)) return "⚠️ Pedido inválido.";
  const [item] = await dbSelect<ItemRow>("content_plan_items", `select=id,plan_id,status,scheduled_at,network,format&id=eq.${id}`);
  if (!item) return "⚠️ Não achei esse pedido.";
  if (item.status !== "planejado") return `Esse pedido já está "${item.status}".`;
  const now = new Date().toISOString();
  if (kind === "rx") {
    await dbUpdateWhere("content_plan_items", `id=eq.${id}`, { status: "cancelado", updated_at: now });
    await dbUpdateWhere("content_plan_items", `source_item_id=eq.${id}&status=in.(planejado,na_fila)`, { status: "cancelado", updated_at: now });
    await audit("nota_referencia_cancelar", "content_plan_items", id, {});
    return "🗑️ Dancinha cancelada, nenhum crédito gasto. Mande outro vídeo quando quiser.";
  }
  const request = await planRequest(item);
  await dbUpdateWhere("content_plan_items", `id=eq.${id}`, { status: "na_fila", note: null, updated_at: now });
  await dbUpdateWhere("content_plans", `id=eq.${item.plan_id}`, { request: { ...request, motion: { ...(request.motion ?? {}), force: true } } });
  await audit("nota_referencia_gerar", "content_plan_items", id, {});
  return "▶️ Combinado: voltou para a fila e o robô gera mesmo assim.";
}
