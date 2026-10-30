import { useState } from "react";
import { CtaChip, CtaModal, brDay, useCtaDays, type CtaDay } from "./ctaDays";
import { Icon } from "./Icon";
import { POST_STATUS } from "./PostFlow";
import { NETWORKS } from "./shared";
import { useStudio, type Post } from "./store";

const WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];
// Faixas do dia na visao semanal (hora de inicio de cada linha).
const BLOCKS = [
  { label: "Manhã", from: 0 },
  { label: "Tarde", from: 12 },
  { label: "Fim de tarde", from: 16 },
  { label: "Noite", from: 20 },
];

function startOfWeek(d: Date) {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return x;
}

function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function addDays(d: Date, n: number) {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

function hhmm(iso: string) {
  return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

function PostChip({ post, onOpen, mini }: { post: Post; onOpen: (p: Post) => void; mini?: boolean }) {
  const net = NETWORKS[post.network];
  const status = post.status === "agendado" ? "" : ` (${POST_STATUS[post.status].toLowerCase()})`;
  return (
    <button
      type="button"
      className={`st-chip st-chip-${post.network} st-chip-${post.status}`}
      onClick={() => onOpen(post)}
      title={`${net.label} · ${post.formats.join(", ")} · ${hhmm(post.scheduledAt!)}${status}${post.caption ? `\n${post.caption}` : ""}`}
    >
      <span className="st-chip-head">
        <Icon name={net.icon} size={13} />
        {hhmm(post.scheduledAt!)}
      </span>
      {!mini && <span className="st-chip-text">{post.formats.join(", ")}</span>}
    </button>
  );
}

interface Props {
  onOpenPost: (post: Post) => void;
  onNew: (date: Date) => void;
}

export function Calendar({ onOpenPost, onNew }: Props) {
  const { posts } = useStudio();
  const [mode, setMode] = useState<"semana" | "mes">("semana");
  const [anchor, setAnchor] = useState(() => new Date());
  const today = new Date();
  const dated = posts.filter((p) => p.scheduledAt && p.status !== "cancelado").sort((a, b) => (a.scheduledAt! < b.scheduledAt! ? -1 : 1));
  const on = (day: Date) => dated.filter((p) => sameDay(new Date(p.scheduledAt!), day));
  // mensagem do dia do bot de vendas (19h) em cada dia visivel
  const monthStart = startOfWeek(new Date(anchor.getFullYear(), anchor.getMonth(), 1));
  const cta = useCtaDays(mode === "semana" ? startOfWeek(anchor) : monthStart, mode === "semana" ? 7 : 42);
  const [ctaOpen, setCtaOpen] = useState<CtaDay | null>(null);

  function move(step: number) {
    setAnchor((a) => (mode === "semana" ? addDays(a, step * 7) : new Date(a.getFullYear(), a.getMonth() + step, 1)));
  }

  const monthLabel = anchor.toLocaleDateString("pt-BR", { month: "long", year: "numeric" });
  const week = Array.from({ length: 7 }, (_, i) => addDays(startOfWeek(anchor), i));

  return (
    <div className="st-calendar">
      <div className="st-cal-toolbar">
        <div className="st-cal-nav">
          <button type="button" className="st-icon-btn" onClick={() => move(-1)} aria-label="Anterior">
            <Icon name="chevronLeft" size={16} />
          </button>
          <button type="button" className="st-icon-btn" onClick={() => move(1)} aria-label="Próximo">
            <Icon name="chevronRight" size={16} />
          </button>
          <span className="st-cal-title">{monthLabel.charAt(0).toUpperCase() + monthLabel.slice(1)}</span>
          <button type="button" className="st-btn st-btn-small" onClick={() => setAnchor(new Date())}>
            Hoje
          </button>
        </div>
        <div className="st-seg">
          <button type="button" className={mode === "semana" ? "active" : ""} onClick={() => setMode("semana")}>
            Semana
          </button>
          <button type="button" className={mode === "mes" ? "active" : ""} onClick={() => setMode("mes")}>
            Mês
          </button>
        </div>
      </div>

      {mode === "semana" ? (
        <div className="st-week-scroll">
          <div className="st-week">
            <div className="st-week-corner" />
            {week.map((d, i) => (
              <div key={i} className={sameDay(d, today) ? "st-week-day today" : "st-week-day"}>
                <span>{WEEKDAYS[i]}</span>
                <b>{d.getDate()}</b>
              </div>
            ))}
            {BLOCKS.map((block, bi) => (
              <div key={block.label} className="st-week-row">
                <div className="st-week-label">{block.label}</div>
                {week.map((d, i) => {
                  const end = BLOCKS[bi + 1]?.from ?? 24;
                  const here = on(d).filter((p) => {
                    const h = new Date(p.scheduledAt!).getHours();
                    return h >= block.from && h < end;
                  });
                  const slot = new Date(d.getFullYear(), d.getMonth(), d.getDate(), Math.max(block.from, 9));
                  const msgs = (cta.get(brDay(d)) ?? []).filter((m) => m.hour >= block.from && m.hour < end);
                  return (
                    <div key={i} className="st-week-cell">
                      {here.map((p) => (
                        <PostChip key={p.id} post={p} onOpen={onOpenPost} />
                      ))}
                      {msgs.map((m) => (
                        <CtaChip key={m.hour} d={m} onOpen={setCtaOpen} />
                      ))}
                      <button type="button" className="st-cell-add" onClick={() => onNew(slot)} aria-label="Agendar aqui">
                        <Icon name="plus" size={14} />
                        Agendar
                      </button>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="st-month">
          {WEEKDAYS.map((w) => (
            <div key={w} className="st-month-head">
              {w}
            </div>
          ))}
          {Array.from({ length: 42 }, (_, i) => addDays(monthStart, i)).map((d, i) => {
            const list = on(d);
            const msgs = cta.get(brDay(d)) ?? [];
            const outside = d.getMonth() !== anchor.getMonth();
            return (
              <div key={i} className={`st-month-cell${outside ? " outside" : ""}${sameDay(d, today) ? " today" : ""}`}>
                <button type="button" className="st-month-day" onClick={() => onNew(new Date(d.getFullYear(), d.getMonth(), d.getDate(), 12))} title="Agendar neste dia">
                  {d.getDate()}
                </button>
                {list.slice(0, 3).map((p) => (
                  <PostChip key={p.id} post={p} onOpen={onOpenPost} mini />
                ))}
                {list.length > 3 && <span className="st-month-more">+{list.length - 3}</span>}
                {msgs.map((m) => (
                  <CtaChip key={m.hour} d={m} mini onOpen={setCtaOpen} />
                ))}
              </div>
            );
          })}
        </div>
      )}
      {ctaOpen && <CtaModal d={ctaOpen} onClose={() => setCtaOpen(null)} />}
    </div>
  );
}
