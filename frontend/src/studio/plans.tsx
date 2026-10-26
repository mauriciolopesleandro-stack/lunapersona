import { useEffect, useState } from "react";
import { Icon } from "./Icon";

// Planejamento pelo chat (parte A): o estudio monta a grade do periodo, a IA
// escreve o prompt da Naiaflow, a legenda e as hashtags de cada item, e o
// administrador revisa e aprova o plano vendo a estimativa de creditos. Os
// itens aprovados vao para a fila da automacao local (parte B).

type SlotKey = "feed" | "stories" | "reels" | "tiktok" | "telegram18";
interface Item {
  id: string;
  position: number;
  scheduled_at: string;
  network: "instagram" | "tiktok" | "telegram";
  format: string;
  kind: "image" | "video";
  adult: boolean;
  prompt: string;
  caption: string;
  hashtags: string[];
  source_item_id: string | null;
  status: string;
  note: string | null;
}
interface Plan {
  id: string;
  title: string;
  status: "rascunho" | "aprovado" | "cancelado";
  items: Item[];
  estimate: { images: number; videos: number; credits: number };
}

const SLOT_LABEL: Record<SlotKey, string> = {
  feed: "Instagram Feed",
  stories: "Instagram Stories",
  reels: "Instagram Reels (vídeo)",
  tiktok: "TikTok",
  telegram18: "Telegram +18",
};
const STATUS: Record<string, string> = {
  planejado: "Planejado",
  na_fila: "Na fila para gerar",
  gerando: "Gerando",
  em_analise: "No Telegram para você aprovar",
  aprovado: "Aprovado",
  reprovado: "Reprovado",
  falhou: "Falhou",
  cancelado: "Cancelado",
};

const iso = (d: Date) => d.toISOString().slice(0, 10);
const plus = (n: number) => new Date(Date.now() + n * 86400_000);

async function call(body: object): Promise<{ plan?: Plan; plans?: Plan[]; pending?: number; written?: number }> {
  const r = await fetch("/api/studio?r=plans", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(b.detail ?? `Erro HTTP ${r.status}`);
  return b;
}

function dest(it: Item): string {
  if (it.adult) return "Telegram +18";
  if (it.network === "tiktok") return "TikTok";
  return `Instagram ${it.format}`;
}

function when(isoDate: string): string {
  return new Date(isoDate).toLocaleString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "America/Sao_Paulo" });
}

export function PlanPanel() {
  const [start, setStart] = useState(iso(plus(1)));
  const [end, setEnd] = useState(iso(plus(4)));
  const [perDay, setPerDay] = useState<Record<SlotKey, number>>({ feed: 2, stories: 3, reels: 0, tiktok: 1, telegram18: 1 });
  const [theme, setTheme] = useState("");
  const [reuse, setReuse] = useState(true);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; prompt: string; caption: string } | null>(null);

  async function reload(selectId?: string) {
    const r = await fetch("/api/studio?r=plans");
    const b = await r.json().catch(() => ({}));
    if (r.ok) {
      setPlans(b.plans ?? []);
      if (selectId) setPlan((b.plans ?? []).find((p: Plan) => p.id === selectId) ?? null);
    }
  }
  useEffect(() => {
    reload().catch(() => undefined);
  }, []);

  // a IA escreve em lotes (cabe no tempo do servidor); o progresso aparece aqui
  async function fill(p: Plan) {
    let current = p;
    for (let round = 0; round < 40; round++) {
      const left = current.items.filter((i) => !i.prompt && !i.source_item_id && i.status !== "cancelado").length;
      if (!left) break;
      const total = current.items.filter((i) => !i.source_item_id && i.status !== "cancelado").length;
      setProgress(`A IA está escrevendo o plano: ${total - left} de ${total}…`);
      const r = await call({ action: "fill", plan_id: current.id });
      current = r.plan!;
      setPlan(current);
      if (!r.written) throw new Error("A IA não conseguiu escrever esta parte. Clique em Continuar para tentar de novo.");
    }
    setProgress(null);
  }

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  const generate = () =>
    run(async () => {
      const r = await call({ action: "generate", start, end, perDay, theme, tiktokReuse: reuse });
      setPlan(r.plan!);
      await fill(r.plan!);
      await reload(r.plan!.id);
    });

  const pending = plan ? plan.items.filter((i) => !i.prompt && !i.source_item_id && i.status !== "cancelado").length : 0;
  const days = plan ? [...new Set(plan.items.map((i) => new Date(i.scheduled_at).toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" })))] : [];

  return (
    <div className="st-plan">
      {!plan && (
        <section className="st-panel st-plan-form">
          <h2>Novo plano</h2>
          <p className="st-hint">
            Escolha o período e quantas publicações por dia em cada destino. O estúdio monta os dias e horários e a IA escreve, para cada uma, o pedido da foto para a
            Naiaflow, a legenda e as hashtags. Você revisa e aprova antes de gastar crédito.
          </p>
          <div className="st-row">
            <label className="st-field">
              <span>Início</span>
              <input type="date" className="st-input" value={start} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label className="st-field">
              <span>Fim</span>
              <input type="date" className="st-input" value={end} onChange={(e) => setEnd(e.target.value)} />
            </label>
          </div>
          <div className="st-plan-counts">
            {(Object.keys(SLOT_LABEL) as SlotKey[]).map((k) => (
              <label key={k} className="st-field">
                <span>{SLOT_LABEL[k]} por dia</span>
                <input
                  type="number"
                  min={0}
                  max={8}
                  className="st-input"
                  value={perDay[k]}
                  onChange={(e) => setPerDay((p) => ({ ...p, [k]: Math.max(0, Math.min(8, Number(e.target.value) || 0)) }))}
                  aria-label={`${SLOT_LABEL[k]} por dia`}
                />
              </label>
            ))}
          </div>
          <label className="st-check">
            <input type="checkbox" checked={reuse} onChange={(e) => setReuse(e.target.checked)} />
            TikTok usa a mesma foto do Feed do dia (gera uma vez, posta nas duas redes)
          </label>
          <label className="st-field">
            <span>Tema ou pedido (opcional)</span>
            <textarea className="st-input" rows={2} value={theme} onChange={(e) => setTheme(e.target.value)} placeholder="Ex.: semana de verão no Rio, praia, academia e noite" aria-label="Tema do plano" />
          </label>
          <button type="button" className="st-btn st-btn-primary" disabled={busy} onClick={generate}>
            <Icon name="calendar" size={16} />
            Gerar plano
          </button>
          {plans.length > 0 && (
            <div className="st-plan-list">
              <span className="st-hint">Planos anteriores:</span>
              {plans.map((p) => (
                <button key={p.id} type="button" className="st-btn st-btn-small" onClick={() => setPlan(p)}>
                  {p.title} · {p.status === "aprovado" ? "aprovado" : p.status === "cancelado" ? "cancelado" : "rascunho"} · {p.items.length} itens
                </button>
              ))}
            </div>
          )}
        </section>
      )}

      {plan && (
        <section className="st-panel st-plan-view">
          <div className="st-plan-head">
            <div>
              <h2>Plano {plan.title}</h2>
              <p className="st-hint">
                {plan.items.length} publicações · {plan.estimate.images} fotos e {plan.estimate.videos} vídeos para gerar · ~{plan.estimate.credits} créditos da Naiaflow
                {plan.status === "aprovado" ? " · aprovado: na fila da automação" : plan.status === "cancelado" ? " · cancelado" : ""}
              </p>
            </div>
            <button type="button" className="st-btn st-btn-small" disabled={busy} onClick={() => setPlan(null)}>
              Voltar
            </button>
          </div>
          {progress && <p className="st-ok">{progress}</p>}
          {error && <p className="st-error">{error}</p>}
          {days.map((day) => (
            <div key={day} className="st-plan-day">
              <h3>{day}</h3>
              {plan.items
                .filter((i) => new Date(i.scheduled_at).toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" }) === day)
                .sort((a, b) => (a.scheduled_at < b.scheduled_at ? -1 : a.scheduled_at > b.scheduled_at ? 1 : a.position - b.position))
                .map((it) => {
                  const src = it.source_item_id ? plan.items.find((s) => s.id === it.source_item_id) : null;
                  const isEditing = editing?.id === it.id;
                  return (
                    <div key={it.id} className={`st-plan-item${it.adult ? " adult" : ""}`}>
                      <div className="st-plan-meta">
                        <b>{when(it.scheduled_at).split(", ").pop()}</b>
                        <span className={`st-badge ${it.adult ? "st-post-falhou" : "st-post-agendado"}`}>{dest(it)}</span>
                        <span className="st-hint">{it.kind === "video" ? "vídeo" : "foto"}</span>
                        {plan.status !== "rascunho" && <span className="st-hint">· {STATUS[it.status] ?? it.status}</span>}
                      </div>
                      {src ? (
                        <p className="st-hint">Mesma foto e legenda do Feed das {when(src.scheduled_at).split(", ").pop()}.</p>
                      ) : isEditing ? (
                        <div className="st-plan-edit">
                          <textarea className="st-input" rows={2} value={editing.prompt} onChange={(e) => setEditing({ ...editing, prompt: e.target.value })} aria-label="Pedido para a Naiaflow" />
                          <textarea className="st-input" rows={2} value={editing.caption} onChange={(e) => setEditing({ ...editing, caption: e.target.value })} aria-label="Legenda" />
                          <div className="st-row">
                            <button
                              type="button"
                              className="st-btn st-btn-small st-btn-primary"
                              disabled={busy}
                              onClick={() =>
                                run(async () => {
                                  const r = await call({ action: "update-item", id: it.id, prompt: editing.prompt, caption: editing.caption });
                                  setPlan(r.plan!);
                                  setEditing(null);
                                })
                              }
                            >
                              Salvar
                            </button>
                            <button type="button" className="st-btn st-btn-small" onClick={() => setEditing(null)}>
                              Cancelar
                            </button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <p className="st-plan-prompt">📸 {it.prompt || <em>escrevendo…</em>}</p>
                          {it.caption && (
                            <p className="st-plan-caption">
                              {it.caption} <span className="st-hint">{it.hashtags.join(" ")}</span>
                            </p>
                          )}
                        </>
                      )}
                      {plan.status === "rascunho" && !isEditing && (
                        <div className="st-row st-plan-actions">
                          {!src && it.prompt && (
                            <button type="button" className="st-link" onClick={() => setEditing({ id: it.id, prompt: it.prompt, caption: it.caption })}>
                              Editar
                            </button>
                          )}
                          <button
                            type="button"
                            className="st-link"
                            disabled={busy}
                            onClick={() =>
                              run(async () => {
                                const r = await call({ action: "remove-item", id: it.id });
                                setPlan(r.plan!);
                              })
                            }
                          >
                            Tirar do plano
                          </button>
                        </div>
                      )}
                      {it.note && <p className="st-hint">Observação: {it.note}</p>}
                    </div>
                  );
                })}
            </div>
          ))}
          {plan.status === "rascunho" && (
            <div className="st-composer-actions">
              {pending > 0 && !busy && (
                <button type="button" className="st-btn" onClick={() => run(() => fill(plan))}>
                  Continuar escrevendo ({pending} faltando)
                </button>
              )}
              <button
                type="button"
                className="st-btn st-btn-danger"
                disabled={busy}
                onClick={() =>
                  window.confirm("Descartar este plano?") &&
                  run(async () => {
                    const r = await call({ action: "cancel", plan_id: plan.id });
                    setPlan(r.plan!);
                    await reload();
                  })
                }
              >
                Descartar plano
              </button>
              <button
                type="button"
                className="st-btn st-btn-primary"
                disabled={busy || pending > 0}
                onClick={() =>
                  window.confirm(`Aprovar o plano? A automação vai gerar ${plan.estimate.images} fotos e ${plan.estimate.videos} vídeos (~${plan.estimate.credits} créditos da Naiaflow) e mandar cada um para você aprovar no Telegram.`) &&
                  run(async () => {
                    const r = await call({ action: "approve", plan_id: plan.id });
                    setPlan(r.plan!);
                    await reload();
                  })
                }
              >
                <Icon name="check" size={16} />
                Aprovar plano (~{plan.estimate.credits} créditos)
              </button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
