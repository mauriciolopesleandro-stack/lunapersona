import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { Modal, NETWORKS, Thumb } from "./shared";
import { useStudio } from "./store";

// Mensagem diaria do bot de vendas (CTA das 19h) no calendario e na Agenda:
// em cada dia, o que saiu (✓ / ✗) ou um exemplo do que vai sair. Cada fa recebe
// a sua (as frases e as previas giram), entao o futuro e so um exemplo.

export type CtaStatus = "enviada" | "falhou" | "nao-rodou" | "sem-fas" | "desligada" | "programada" | "aguardando" | "sem-registro";
export interface CtaDay {
  day: string;
  hour: number;
  status: CtaStatus;
  sent: number;
  failed: number;
  reason: string | null;
  text: string;
  media_id: string | null;
  kind: "photo" | "video" | null;
}

const MARK: Record<CtaStatus, string> = {
  enviada: "✓",
  falhou: "✗",
  "nao-rodou": "✗",
  "sem-fas": "–",
  desligada: "–",
  programada: "",
  aguardando: "…",
  "sem-registro": "",
};

export function ctaStatusText(d: CtaDay): string {
  const fas = (n: number) => `${n} fã${n === 1 ? "" : "s"}`;
  switch (d.status) {
    case "enviada":
      return `✓ Saiu para ${fas(d.sent)}${d.failed ? ` (${d.failed} falharam)` : ""}`;
    case "falhou":
      return `✗ Falhou${d.reason ? `: ${d.reason}` : ""}`;
    case "nao-rodou":
      return "✗ Não saiu (o processador não rodou nesse dia)";
    case "sem-fas":
      return "– Ninguém para receber (todos VIP ou já tinham recebido)";
    case "desligada":
      return "– Mensagem diária desligada";
    case "programada":
      return `Programada para as ${d.hour}h · exemplo (cada fã recebe a sua)`;
    case "aguardando":
      return `Saindo a partir das ${d.hour}h…`;
    default:
      return "Sem registro (antes do controle por dia)";
  }
}

// dia de Brasilia (AAAA-MM-DD) de uma data local
export const brDay = (d: Date) =>
  new Date(d.getFullYear(), d.getMonth(), d.getDate(), 12).toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" });

export function useCtaDays(start: Date, days: number): Map<string, CtaDay> {
  const [map, setMap] = useState<Map<string, CtaDay>>(new Map());
  const key = brDay(start);
  useEffect(() => {
    let alive = true;
    fetch("/api/studio?r=sales", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "cta-days", start: key, days }) })
      .then((r) => (r.ok ? r.json() : { days: [] }))
      .then((b: { days?: CtaDay[] }) => alive && setMap(new Map((b.days ?? []).map((d) => [d.day, d]))))
      .catch(() => undefined); // bot de vendas nao configurado: o calendario fica sem a linha
    return () => {
      alive = false;
    };
  }, [key, days]);
  return map;
}

const time = (d: CtaDay) => `${String(d.hour).padStart(2, "0")}:00`;

// chip do calendario (semana e mes)
export function CtaChip({ d, mini, onOpen }: { d: CtaDay; mini?: boolean; onOpen: (d: CtaDay) => void }) {
  return (
    <button type="button" className={`st-chip st-chip-telegram st-chip-cta cta-${d.status}`} onClick={() => onOpen(d)} title={`Mensagem do dia (bot de vendas)\n${ctaStatusText(d)}`}>
      <span className="st-chip-head">
        <Icon name={NETWORKS.telegram.icon} size={13} />
        {time(d)}
        {MARK[d.status] && <b className="st-cta-mark">{MARK[d.status]}</b>}
      </span>
      {!mini && <span className="st-chip-text">Mensagem do dia</span>}
    </button>
  );
}

// linha da Agenda
export function CtaRow({ d, onOpen }: { d: CtaDay; onOpen: (d: CtaDay) => void }) {
  return (
    <button type="button" className={`st-agenda-post st-agenda-cta cta-${d.status}`} onClick={() => onOpen(d)}>
      <time>{time(d)}</time>
      <Icon name={NETWORKS.telegram.icon} size={14} />
      <span>Mensagem do dia · {ctaStatusText(d)}</span>
    </button>
  );
}

// detalhe: a previa que foi (ou vai) junto e o texto
export function CtaModal({ d, onClose }: { d: CtaDay; onClose: () => void }) {
  const { items } = useStudio();
  const item = d.media_id ? items.find((i) => i.id === d.media_id) : undefined;
  const date = new Date(`${d.day}T12:00:00-03:00`).toLocaleDateString("pt-BR", { weekday: "long", day: "2-digit", month: "2-digit" });
  return (
    <Modal title={`Mensagem do dia — ${date}, ${time(d)}`} onClose={onClose}>
      <div className="st-cta-detail">
        <p className={`st-cta-state cta-${d.status}`}>{ctaStatusText(d)}</p>
        <div className="st-cta-preview">
          {item ? <Thumb item={item} /> : <span className="st-hint">{d.media_id ? "Prévia fora da pasta do estúdio" : "Sem prévia (vai só o texto)"}</span>}
          <p>{d.text.split("{nome}").join("(nome do fã)")}</p>
        </div>
        <p className="st-hint">
          Vai pelo bot de vendas para quem confirmou os 18 anos e ainda não é VIP, com os botões dos pacotes. Os textos e as prévias ficam em Monetização → Vendas e Conteúdo do Telegram.
        </p>
      </div>
    </Modal>
  );
}
