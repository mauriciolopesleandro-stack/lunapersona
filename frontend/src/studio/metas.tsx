import { useEffect, useState } from "react";
import { Icon } from "./Icon";

// Metas do Instagram (3 Feed, 3 Stories e 1 Reels por dia): quanto de cada dia
// ja esta garantido (publicacao criada ou item na fila do robo) e qual o
// proximo dia a completar. O conteudo novo vai sozinho para o proximo buraco.

type Goal = "Feed" | "Stories" | "Reels";
const GOALS: Record<Goal, number> = { Feed: 3, Stories: 3, Reels: 1 };
interface Day {
  day: string;
  counts: Record<Goal, number>;
  missing: Record<Goal, number>;
  complete: boolean;
}

function label(day: string, i: number): string {
  if (i === 0) return "Hoje";
  if (i === 1) return "Amanhã";
  return new Date(`${day}T12:00:00-03:00`).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" }).replace(".", "");
}

function missingText(d: Day): string {
  const parts = (Object.keys(GOALS) as Goal[]).filter((g) => d.missing[g] > 0).map((g) => `${d.missing[g]} ${g}`);
  return parts.length ? `falta ${parts.join(", ")}` : "completo";
}

export function MetasPanel({ days = 14, compact = false }: { days?: number; compact?: boolean }) {
  const [list, setList] = useState<Day[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/studio?r=plans", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "coverage", days }) })
      .then(async (r) => {
        const b = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(b.detail ?? `Erro HTTP ${r.status}`);
        setList(b.days);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [days]);

  if (error) return compact ? null : <p className="st-error">{error}</p>;
  if (!list) return <div className="st-panel st-metas st-metas-loading" aria-busy="true" />;
  const next = list.findIndex((d) => !d.complete);
  const done = list.filter((d) => d.complete).length;

  return (
    <section className={compact ? "st-panel st-metas compact" : "st-panel st-metas"}>
      <div className="st-panel-head">
        <h2>
          <Icon name="calendar" size={17} /> Metas do Instagram
        </h2>
        <span className="st-hint">3 Feed · 3 Stories · 1 Reels por dia</span>
      </div>
      <p className={next === -1 ? "st-metas-next ok" : "st-metas-next"}>
        {next === -1
          ? `Os próximos ${list.length} dias estão completos 👏`
          : `Próximo a completar: ${label(list[next].day, next)} — ${missingText(list[next])}. ${done} de ${list.length} dias completos.`}
      </p>
      <ul className="st-metas-days">
        {list.map((d, i) => (
          <li key={d.day} className={d.complete ? "ok" : i === next ? "next" : ""}>
            <b>{label(d.day, i)}</b>
            {(Object.keys(GOALS) as Goal[]).map((g) => (
              <span key={g} className={d.counts[g] >= GOALS[g] ? "chip ok" : "chip"}>
                {g} {Math.min(d.counts[g], GOALS[g])}/{GOALS[g]}
              </span>
            ))}
            <i>{d.complete ? "✓" : missingText(d)}</i>
          </li>
        ))}
      </ul>
      {!compact && <p className="st-hint">Fotos e dancinhas novas vão sozinhas para o próximo horário vazio. O bot avisa às 9h (hoje) e às 20h (amanhã) o que falta.</p>}
    </section>
  );
}
