import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { call, type Status } from "./sales";

// Monetizacao: os numeros do bot de vendas (Telegram) em cards para a Visao
// Geral, a lista de quem esta no VIP (Assinaturas) e o resumo do dinheiro
// (Financeiro). Tudo sai do mesmo status de Vendas (/api/studio?r=sales).

const DAY = 86400_000;
const brl = (cents: number) => (cents / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const day = (iso: string) => new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
const SOLD = new Set(["pago", "entregue", "expirado"]);

// um pedido so por vez para as telas que abrem juntas
let shared: Promise<Status> | null = null;
let sharedAt = 0;
export function useSalesStatus(): { status: Status | null; error: string | null } {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!shared || Date.now() - sharedAt > 60_000) {
      sharedAt = Date.now();
      shared = call();
      shared.catch(() => (shared = null));
    }
    let alive = true;
    shared.then((s) => alive && setStatus(s)).catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, []);
  return { status, error };
}

function vipLeft(until: string): number {
  return Math.ceil((Date.parse(until) - Date.now()) / DAY);
}

// --- Cards do Telegram na Visao Geral ------------------------------------------------

export function TelegramCards({ onOpen }: { onOpen: (page: string) => void }) {
  const { status, error } = useSalesStatus();
  if (error) return null; // banco ou bot ainda nao configurados: a Visao Geral segue sem os cards
  if (!status) {
    return (
      <section className="st-tg-cards" aria-busy="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="st-panel st-tg-card st-tg-card-loading" />
        ))}
      </section>
    );
  }
  const online = status.tokenSet && status.connected;
  const expiring = status.vipMembers.filter((m) => vipLeft(m.until) <= 3).length;
  const withText = status.previews.filter((p) => p.text).length;
  const today = status.orders.filter((o) => SOLD.has(o.status) && Date.now() - Date.parse(o.paid_at ?? o.created_at) < DAY);

  // o que pede atencao primeiro (o card ganha destaque)
  const hint = !online
    ? { card: "bot", text: "Bot de vendas fora do ar: conecte em Monetização." }
    : !status.pix.configured
      ? { card: "vendas", text: "Pix desligado: ninguém consegue comprar." }
      : status.previews.length === 0
        ? { card: "previas", text: "Sem prévias: a mensagem diária sai só com texto." }
        : expiring
          ? { card: "vip", text: `${expiring} VIP vence${expiring > 1 ? "m" : ""} em até 3 dias (o bot já está cobrando).` }
          : null;

  return (
    <section className="st-tg">
      <div className="st-panel-head">
        <h2>
          <Icon name="telegram" size={18} /> Telegram
        </h2>
        {hint && <span className="st-tg-hint">{hint.text}</span>}
      </div>
      <div className="st-tg-cards">
        <button type="button" className={`st-panel st-tg-card${hint?.card === "bot" ? " alert" : ""}`} onClick={() => onOpen("vendas")}>
          <span className="st-tg-label">
            <i className={online ? "st-dot on" : "st-dot"} /> Bot de vendas
          </span>
          <b>{status.fans.adults}</b>
          <small>
            fãs +18 · {status.fans.today} novo{status.fans.today === 1 ? "" : "s"} hoje
            {status.bot ? ` · @${status.bot}` : ""}
          </small>
        </button>
        <button type="button" className={`st-panel st-tg-card${hint?.card === "vendas" ? " alert" : ""}`} onClick={() => onOpen("financeiro")}>
          <span className="st-tg-label">Vendas em 30 dias</span>
          <b>{brl(status.sales.total30)}</b>
          <small>
            {status.sales.count30} venda{status.sales.count30 === 1 ? "" : "s"}
            {today.length ? ` · ${today.length} hoje` : ""}
            {status.sales.pending ? ` · ${status.sales.pending} Pix esperando` : ""}
          </small>
        </button>
        <button type="button" className={`st-panel st-tg-card${hint?.card === "vip" ? " alert" : ""}`} onClick={() => onOpen("assinaturas")}>
          <span className="st-tg-label">VIP {status.channel ? `· ${status.channel}` : ""}</span>
          <b>{status.vipMembers.length}</b>
          <small>{expiring ? `${expiring} vencendo em até 3 dias` : "no canal agora"}</small>
        </button>
        <button type="button" className={`st-panel st-tg-card${hint?.card === "previas" ? " alert" : ""}`} onClick={() => onOpen("telegram")}>
          <span className="st-tg-label">Prévias e chamadas</span>
          <b>{status.previews.length}</b>
          <small>
            {withText} com mini-história · diária {status.cta.enabled ? `às ${status.cta.hour}h` : "desligada"}
          </small>
        </button>
      </div>
    </section>
  );
}

// --- Assinaturas: quem esta no VIP ----------------------------------------------------

export function SubscriptionsPage() {
  const { status, error } = useSalesStatus();
  if (error) return <p className="st-error">{error}</p>;
  if (!status) return <div className="st-empty st-empty-small">Carregando…</div>;
  const vip = status.products.find((p) => p.kind === "vip" && p.active);
  const members = status.vipMembers;
  const groups = [
    { label: "Já venceu (ainda no canal por até 3 dias)", list: members.filter((m) => vipLeft(m.until) <= 0) },
    { label: "Vence em até 3 dias (recebendo o Pix de renovação)", list: members.filter((m) => vipLeft(m.until) > 0 && vipLeft(m.until) <= 3) },
    { label: "Em dia", list: members.filter((m) => vipLeft(m.until) > 3) },
  ];
  return (
    <div>
      <div className="st-stats">
        <div className="st-panel st-mini-stat">
          <span>No VIP agora</span>
          <b>{members.length}</b>
        </div>
        <div className="st-panel st-mini-stat">
          <span>Receita mensal do VIP</span>
          <b>{vip ? brl(members.filter((m) => vipLeft(m.until) > 0).length * vip.price_cents) : "—"}</b>
        </div>
        <div className="st-panel st-mini-stat">
          <span>Plano</span>
          <b className="st-mini-stat-text">{vip ? `${brl(vip.price_cents)} · ${vip.period_days} dias` : "VIP desligado"}</b>
        </div>
      </div>
      <p className="st-hint">
        O bot avisa cada pessoa no privado 3 dias antes de vencer, com o Pix de renovação, e repete todo dia. Quem não renova sai do canal 3 dias depois
        do vencimento.
      </p>
      {members.length === 0 ? (
        <div className="st-empty st-empty-small">Ninguém no VIP ainda.</div>
      ) : (
        groups
          .filter((g) => g.list.length)
          .map((g) => (
            <section key={g.label} className="st-panel st-settings">
              <h2>
                {g.label} ({g.list.length})
              </h2>
              <ul className="st-member-list">
                {g.list.map((m) => {
                  const left = vipLeft(m.until);
                  return (
                    <li key={m.fan + m.until}>
                      <span className="st-member-avatar">{m.fan.replace("@", "").slice(0, 1).toUpperCase()}</span>
                      <b>{m.fan}</b>
                      <small>{left > 0 ? `vence ${day(m.until)} · faltam ${left} dia${left > 1 ? "s" : ""}` : `venceu ${day(m.until)}`}</small>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))
      )}
    </div>
  );
}

// --- Financeiro: o dinheiro das vendas -------------------------------------------------

export function FinancePage() {
  const { status, error } = useSalesStatus();
  if (error) return <p className="st-error">{error}</p>;
  if (!status) return <div className="st-empty st-empty-small">Carregando…</div>;
  const sold = status.orders.filter((o) => SOLD.has(o.status));
  const in30 = sold.filter((o) => Date.now() - Date.parse(o.paid_at ?? o.created_at) < 30 * DAY);
  const in7 = in30.filter((o) => Date.now() - Date.parse(o.paid_at ?? o.created_at) < 7 * DAY);
  const total = (list: typeof sold) => list.reduce((t, o) => t + o.amount_cents, 0);
  const byProduct = [...new Set(in30.map((o) => o.product))]
    .map((name) => ({ name, list: in30.filter((o) => o.product === name) }))
    .sort((a, b) => total(b.list) - total(a.list));
  const max = Math.max(1, ...byProduct.map((p) => total(p.list)));
  const pending = status.orders.filter((o) => o.status === "pendente");
  // ultimos 14 dias, um traco por dia
  const days = Array.from({ length: 14 }, (_, i) => {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - (13 - i));
    const end = start.getTime() + DAY;
    return { label: day(start.toISOString()), value: total(sold.filter((o) => { const t = Date.parse(o.paid_at ?? o.created_at); return t >= start.getTime() && t < end; })) };
  });
  const dayMax = Math.max(1, ...days.map((d) => d.value));

  return (
    <div>
      <div className="st-stats">
        <div className="st-panel st-mini-stat">
          <span>Últimos 30 dias</span>
          <b>{brl(total(in30))}</b>
          <small>{in30.length} venda(s)</small>
        </div>
        <div className="st-panel st-mini-stat">
          <span>Últimos 7 dias</span>
          <b>{brl(total(in7))}</b>
          <small>{in7.length} venda(s)</small>
        </div>
        <div className="st-panel st-mini-stat">
          <span>Ticket médio</span>
          <b>{in30.length ? brl(Math.round(total(in30) / in30.length)) : "—"}</b>
          <small>nos últimos 30 dias</small>
        </div>
        <div className="st-panel st-mini-stat">
          <span>Pix esperando</span>
          <b>{pending.length}</b>
          <small>{brl(total(pending))} em aberto</small>
        </div>
      </div>

      <div className="st-finance">
        <section className="st-panel st-settings">
          <h2>Por dia (14 dias)</h2>
          <div className="st-bars" role="img" aria-label="Vendas por dia nos últimos 14 dias">
            {days.map((d) => (
              <span key={d.label} title={`${d.label}: ${brl(d.value)}`}>
                <i style={{ height: `${Math.max(2, (d.value / dayMax) * 100)}%` }} className={d.value ? "on" : ""} />
                <small>{d.label.slice(0, 2)}</small>
              </span>
            ))}
          </div>
        </section>
        <section className="st-panel st-settings">
          <h2>Por pacote (30 dias)</h2>
          {byProduct.length === 0 ? (
            <div className="st-empty st-empty-small">Nenhuma venda nos últimos 30 dias.</div>
          ) : (
            <ul className="st-product-bars">
              {byProduct.map((p) => (
                <li key={p.name}>
                  <span>
                    {p.name} <small>· {p.list.length}x</small>
                  </span>
                  <b>{brl(total(p.list))}</b>
                  <i style={{ width: `${(total(p.list) / max) * 100}%` }} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      <p className="st-hint">Valores brutos, antes da taxa da PushinPay. Custos (créditos de IA, Naiaflow) ainda não entram aqui.</p>
    </div>
  );
}
