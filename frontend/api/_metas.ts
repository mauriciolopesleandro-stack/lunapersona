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
// publicacao (na fila ou gerando). Stories conta por foto (31/10): cada foto da
// publicacao sai como um Stories; o pedido na fila conta as 3 do Criar.
export const STORIES_PER_REQUEST = 3;
export async function coverage(days = 14, start = todayInBrazil()): Promise<DayCoverage[]> {
  // gt com 1 s de folga (= a partir da meia-noite)
  const from = new Date(Date.parse(`${start}T00:00:00${OFFSET}`) - 1000).toISOString();
  const to = new Date(Date.parse(`${addDays(start, days)}T00:00:00${OFFSET}`)).toISOString();
  const [posts, items] = await Promise.all([
    dbSelect<{ id: string; scheduled_at: string }>(
      "posts",
      `select=id,scheduled_at&network=eq.instagram&status=not.in.(cancelado,falhou,rascunho)&scheduled_at=gt.${from}&scheduled_at=lt.${to}`
    ),
    dbSelect<{ scheduled_at: string; format: string; source_item_id: string | null }>(
      "content_plan_items",
      `select=scheduled_at,format,source_item_id&network=eq.instagram&status=in.(na_fila,gerando)&post_id=is.null&scheduled_at=gt.${from}&scheduled_at=lt.${to}`
    ),
  ]);
  const ids = posts.map((p) => p.id).join(",");
  const [dests, media] = posts.length
    ? await Promise.all([
        dbSelect<{ post_id: string; format: string }>("post_destinations", `select=post_id,format&status=neq.cancelado&post_id=in.(${ids})`),
        dbSelect<{ post_id: string }>("post_media", `select=post_id&post_id=in.(${ids})`),
      ])
    : [[], []];
  const when = new Map(posts.map((p) => [p.id, p.scheduled_at]));
  const photos = new Map<string, number>();
  for (const m of media) photos.set(m.post_id, (photos.get(m.post_id) ?? 0) + 1);
  const entries: { at: string; format: string; n: number }[] = [
    ...dests.map((d) => ({ at: when.get(d.post_id)!, format: d.format, n: d.format === "Stories" ? Math.max(1, photos.get(d.post_id) ?? 1) : 1 })),
    // Stories na fila: o robo traz as 3 fotos do Criar (o que reaproveita a foto do Feed leva 1)
    ...items.map((i) => ({ at: i.scheduled_at, format: i.format, n: i.format === "Stories" && !i.source_item_id ? STORIES_PER_REQUEST : 1 })),
  ];
  const out: DayCoverage[] = [];
  for (let i = 0; i < days; i++) {
    const day = addDays(start, i);
    const counts = { Feed: 0, Stories: 0, Reels: 0 } as Record<Goal, number>;
    const times = { Feed: [], Stories: [], Reels: [] } as Record<Goal, string[]>;
    for (const e of entries) {
      if (!(e.format in GOALS) || dayOf(e.at) !== day) continue;
      counts[e.format as Goal] += e.n;
      times[e.format as Goal].push(hhmm(e.at));
    }
    // hoje: horario padrao que ja passou nao da mais para preencher (nao conta como falta)
    const isToday = day === todayInBrazil();
    const nowMs = Date.now();
    const open = (g: Goal) =>
      isToday ? GOAL_TIMES[g].filter((t) => Date.parse(`${day}T${t}:00${OFFSET}`) > nowMs && !times[g].includes(t)).length : Infinity;
    const missing = Object.fromEntries(
      (Object.keys(GOALS) as Goal[]).map((g) => [g, Math.min(Math.max(0, GOALS[g] - counts[g]), open(g))])
    ) as Record<Goal, number>;
    out.push({ day, counts, times, missing, complete: Object.values(missing).every((n) => n === 0) });
  }
  return out;
}

// Proximo horario vazio do formato: o primeiro dia (a partir de agora) que
// ainda nao bateu a meta, no primeiro horario padrao livre (e que ainda nao
// passou). taken: horarios ja reservados nesta mesma chamada. Stories: cada
// reserva vale as 3 fotos do Criar.
export async function nextSlot(goal: Goal, taken: string[] = []): Promise<string> {
  const now = Date.now() + 20 * 60_000; // folga para o robo gerar
  for (let page = 0; page < 6; page++) {
    const days = await coverage(30, addDays(todayInBrazil(), page * 30));
    for (const d of days) {
      const reserved = taken.filter((t) => t.startsWith(d.day)).length * (goal === "Stories" ? STORIES_PER_REQUEST : 1);
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

// /agenda no bot de aprovacao (01/11): 7 dias com o que ja esta garantido de
// cada formato e os buracos; o proximo buraco de cada formato e onde o pedido
// novo entra sozinho (nextSlot, so leitura: nada e reservado).
export const AGENDA_WEEKS = 4;
const ICON: Record<Goal, string> = { Feed: "📷", Stories: "📱", Reels: "🎬" };
export async function agendaText(week = 0): Promise<string> {
  const days = await coverage(7, addDays(todayInBrazil(), week * 7));
  const lines = [`📅 Agenda do Instagram — ${WEEKDAY(days[0].day)} a ${WEEKDAY(days[6].day)}`];
  for (const d of days) {
    lines.push("", `${d.complete ? "✅" : "🕳️"} ${WEEKDAY(d.day)}${d.complete ? " — completo" : ""}`);
    for (const g of Object.keys(GOALS) as Goal[]) {
      const times = [...d.times[g]].sort().join(" · ");
      lines.push(`   ${ICON[g]} ${g} ${d.counts[g]}/${GOALS[g]}${times ? ` (${times})` : ""}${d.missing[g] > 0 ? ` — falta ${d.missing[g]}` : ""}`);
    }
  }
  const next = async (g: Goal) => {
    try {
      const at = await nextSlot(g, []);
      return `${WEEKDAY(dayOf(at))} às ${hhmm(at)}`;
    } catch {
      return "nenhum nos próximos meses";
    }
  };
  lines.push(
    "",
    "Próximo buraco (o pedido novo entra nele sozinho):",
    `${ICON.Feed} Feed: ${await next("Feed")}`,
    `${ICON.Stories} Stories: ${await next("Stories")}`,
    `${ICON.Reels} Reels: ${await next("Reels")}`,
    "",
    "Para tampar: mande uma foto aqui e escolha Feed ou Stories. Reels: dancinha pelo estúdio."
  );
  return lines.join("\n");
}

export function agendaButtons(week: number) {
  const row = [];
  if (week > 0) row.push({ text: "◀ Semana anterior", callback_data: `ag:${week - 1}` });
  row.push({ text: "🔄 Atualizar", callback_data: `ag:${week}` });
  if (week < AGENDA_WEEKS) row.push({ text: "Próxima semana ▶", callback_data: `ag:${week + 1}` });
  return { inline_keyboard: [row] };
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
