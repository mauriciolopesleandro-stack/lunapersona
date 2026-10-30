import { dbSelect } from "./_supabase.js";

// Metas do dia (pedido do administrador, 30/10): todo dia 3 Feed, 3 Stories e
// 1 Reels no Instagram. O painel mostra quanto de cada dia ja esta garantido
// (publicacao criada ou item do plano aprovado na fila do robo), o conteudo novo
// vai sozinho para o proximo horario vazio do formato e o bot avisa as 9h (hoje)
// e as 20h (amanha) o que falta.

export type Goal = "Feed" | "Stories" | "Reels";
export const GOALS: Record<Goal, number> = { Feed: 3, Stories: 3, Reels: 1 };
// horarios padrao de cada formato (os mesmos do Planejamento)
export const GOAL_TIMES: Record<Goal, string[]> = { Feed: ["12:00", "15:30", "19:00"], Stories: ["09:00", "13:00", "17:00"], Reels: ["18:00"] };
const TZ = "America/Sao_Paulo";
const OFFSET = "-03:00"; // Brasilia (sem horario de verao)
const DAY = 86400_000;

export interface DayCoverage {
  day: string; // AAAA-MM-DD
  counts: Record<Goal, number>;
  times: Record<Goal, string[]>; // horarios ja ocupados (HH:MM)
  missing: Record<Goal, number>;
  complete: boolean;
}

const dayOf = (iso: string) => new Date(iso).toLocaleDateString("sv-SE", { timeZone: TZ });
const hhmm = (iso: string) => new Date(iso).toLocaleTimeString("pt-BR", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
export const todayInBrazil = (at = Date.now()) => new Date(at).toLocaleDateString("sv-SE", { timeZone: TZ });
const addDays = (day: string, n: number) => new Date(Date.parse(`${day}T12:00:00${OFFSET}`) + n * DAY).toLocaleDateString("sv-SE", { timeZone: TZ });

// O que ja esta garantido em cada dia: publicacoes do Instagram (menos
// canceladas, falhas e rascunhos) e itens de planos aprovados ainda sem
// publicacao (na fila ou gerando).
export async function coverage(days = 14, start = todayInBrazil()): Promise<DayCoverage[]> {
  const from = new Date(Date.parse(`${start}T00:00:00${OFFSET}`)).toISOString();
  const to = new Date(Date.parse(`${addDays(start, days)}T00:00:00${OFFSET}`)).toISOString();
  const [posts, items] = await Promise.all([
    dbSelect<{ id: string; scheduled_at: string }>(
      "posts",
      `select=id,scheduled_at&network=eq.instagram&status=not.in.(cancelado,falhou,rascunho)&scheduled_at=gte.${from}&scheduled_at=lt.${to}`
    ),
    dbSelect<{ scheduled_at: string; format: string }>(
      "content_plan_items",
      `select=scheduled_at,format&network=eq.instagram&status=in.(na_fila,gerando)&post_id=is.null&scheduled_at=gte.${from}&scheduled_at=lt.${to}`
    ),
  ]);
  const dests = posts.length
    ? await dbSelect<{ post_id: string; format: string }>("post_destinations", `select=post_id,format&status=neq.cancelado&post_id=in.(${posts.map((p) => p.id).join(",")})`)
    : [];
  const when = new Map(posts.map((p) => [p.id, p.scheduled_at]));
  const entries: { at: string; format: string }[] = [
    ...dests.map((d) => ({ at: when.get(d.post_id)!, format: d.format })),
    ...items.map((i) => ({ at: i.scheduled_at, format: i.format })),
  ];
  const out: DayCoverage[] = [];
  for (let i = 0; i < days; i++) {
    const day = addDays(start, i);
    const counts = { Feed: 0, Stories: 0, Reels: 0 } as Record<Goal, number>;
    const times = { Feed: [], Stories: [], Reels: [] } as Record<Goal, string[]>;
    for (const e of entries) {
      if (!(e.format in GOALS) || dayOf(e.at) !== day) continue;
      counts[e.format as Goal]++;
      times[e.format as Goal].push(hhmm(e.at));
    }
    const missing = Object.fromEntries((Object.keys(GOALS) as Goal[]).map((g) => [g, Math.max(0, GOALS[g] - counts[g])])) as Record<Goal, number>;
    out.push({ day, counts, times, missing, complete: Object.values(missing).every((n) => n === 0) });
  }
  return out;
}

// Proximo horario vazio do formato: o primeiro dia (a partir de agora) que
// ainda nao bateu a meta, no primeiro horario padrao livre (e que ainda nao
// passou). taken: horarios ja reservados nesta mesma chamada.
export async function nextSlot(goal: Goal, taken: string[] = []): Promise<string> {
  const now = Date.now() + 20 * 60_000; // folga para o robo gerar
  for (let page = 0; page < 6; page++) {
    const days = await coverage(30, addDays(todayInBrazil(), page * 30));
    for (const d of days) {
      const reserved = taken.filter((t) => t.startsWith(d.day)).length;
      if (d.counts[goal] + reserved >= GOALS[goal]) continue;
      for (const time of GOAL_TIMES[goal]) {
        const iso = new Date(`${d.day}T${time}:00${OFFSET}`).toISOString();
        if (Date.parse(iso) < now || d.times[goal].includes(time) || taken.includes(`${d.day} ${time}`)) continue;
        taken.push(`${d.day} ${time}`);
        return iso;
      }
    }
  }
  throw new Error("Não achei horário vazio nos próximos 6 meses.");
}

const WEEKDAY = (day: string) => new Date(`${day}T12:00:00${OFFSET}`).toLocaleDateString("pt-BR", { timeZone: TZ, weekday: "long", day: "2-digit", month: "2-digit" });

export function missingText(d: DayCoverage): string {
  const parts = (Object.keys(GOALS) as Goal[]).filter((g) => d.missing[g] > 0).map((g) => `${d.missing[g]} ${g}`);
  return parts.length ? `falta ${parts.join(", ")}` : "completo ✅";
}

// Aviso do bot: 9h fala de hoje, 20h de amanha; os dois listam os proximos dias com buraco.
export async function goalsNotice(which: "hoje" | "amanha"): Promise<string> {
  const days = await coverage(8);
  const target = which === "hoje" ? days[0] : days[1];
  const holes = days.slice(which === "hoje" ? 1 : 2).filter((d) => !d.complete).slice(0, 4);
  const lines = [
    `📅 Metas do Instagram — ${which === "hoje" ? "hoje" : "amanhã"} (${WEEKDAY(target.day)}): ${missingText(target)}`,
    `Feed ${target.counts.Feed}/3 · Stories ${target.counts.Stories}/3 · Reels ${target.counts.Reels}/1`,
  ];
  if (holes.length) lines.push("", "Próximos dias com buraco:", ...holes.map((d) => `• ${WEEKDAY(d.day)}: ${missingText(d)}`));
  else lines.push("", "Os próximos dias estão completos 👏");
  if (target.missing.Reels > 0) lines.push("", "Reels: mande um vídeo ou link de dancinha na Conversa (💃 Fazer dancinha).");
  return lines.join("\n");
}
