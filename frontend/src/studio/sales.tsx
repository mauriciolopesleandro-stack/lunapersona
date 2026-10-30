import { useEffect, useMemo, useState } from "react";
import { putSignedUpload } from "./cloud";
import { Icon } from "./Icon";
import { Modal, Thumb } from "./shared";
import { useStudio, type Item } from "./store";

// Vendas (+18): o bot de vendas que os fas abrem no Telegram, o Pix
// (PushinPay), os pacotes (ensaio, album e VIP por assinatura) com o conteudo
// de cada um e os numeros de fas e vendas. As fotos dos pacotes saem da pasta do PC, sobem
// uma vez para o Telegram (o estudio guarda so o codigo do arquivo) e a copia
// temporaria e apagada.

export interface Product {
  id: string;
  slug: string;
  kind: "ensaio" | "album" | "vip";
  title: string;
  description: string;
  price_cents: number;
  period_days: number;
  billing: "avulso" | "mensal";
  active: boolean;
  media: { media_id: string; kind: "photo" | "video"; path: string | null }[];
}
export interface Cta {
  enabled: boolean;
  hours: number[];
  hour: number;
  welcome: string;
  pixReminder: string;
  daily: string[];
  renewal: string[];
  renewalGrace: string;
}

export interface Status {
  storeReady: boolean;
  tokenSet: boolean;
  bot: string | null;
  connected: boolean;
  adminStarted: boolean;
  channel: string | null;
  products: Product[];
  fans: { total: number; adults: number; optedOut: number; blocked: number; today: number };
  pix: { configured: boolean; env: "producao" | "sandbox" };
  sales: { count30: number; total30: number; pending: number };
  vipPosts: { media_id: string; path: string | null }[]; // arquivos da pasta do VIP ja postados no canal
  previews: { media_id: string; kind: "photo" | "video"; path: string | null; text: string }[]; // previas das mensagens (CTA) e a chamada de cada uma
  cta: Cta;
  orders: { id: string; status: string; amount_cents: number; created_at: string; paid_at: string | null; access_until: string | null; product: string; fan: string; note: string | null }[];
  vipMembers: { fan: string; until: string }[]; // quem esta no canal VIP agora (vencido ha ate 3 dias ainda conta)
  channelMembers: number | null; // o Telegram conta os membros do canal VIP (com os administradores)
  promo: Promo;
}
export interface Promo {
  enabled: boolean;
  days: number[]; // 0 = domingo
  label: string;
  prices: Record<string, number>;
  active: boolean; // valendo agora (horario de Brasilia)
  today: number;
}

const ORDER_STATUS: Record<string, string> = {
  pendente: "Esperando o Pix",
  pago: "Pago (entregando)",
  entregue: "Pago e entregue",
  expirado: "Acesso vencido",
  cancelado: "Cancelado",
};

const kindLabel = (p: Pick<Product, "kind" | "billing">) =>
  p.kind === "vip" ? (p.billing === "mensal" ? "VIP mensal (Pix Recorrente)" : "VIP avulso") : p.kind === "album" ? "Álbum" : "Ensaio";
const PHOTO_MAX = 5 * 1024 * 1024;
const VIDEO_MAX = 20 * 1024 * 1024;

export async function call(body?: object): Promise<Status & { url?: string; path?: string; warning?: string | null }> {
  const r = await fetch("/api/studio?r=sales", body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(b.detail ?? `Erro HTTP ${r.status}`);
  return b;
}

const money = (cents: number) => (cents / 100).toFixed(2).replace(".", ",");
function cents(text: string): number | null {
  const n = Number(text.replace(/[^\d,.]/g, "").replace(",", "."));
  return Number.isFinite(n) && n > 0 ? Math.round(n * 100) : null;
}

// foto em JPEG ate 5 MB (limite do Telegram por link); video MP4 ate 20 MB
export async function prepare(item: Item): Promise<{ blob: Blob; mime: string }> {
  const blob = await fetch(item.url).then((r) => r.blob());
  if (item.kind === "video") {
    if (blob.type !== "video/mp4") throw new Error(`“${item.title}”: o vídeo precisa ser MP4.`);
    if (blob.size > VIDEO_MAX) throw new Error(`“${item.title}”: o vídeo passa de 20 MB.`);
    return { blob, mime: "video/mp4" };
  }
  if (blob.type === "image/jpeg" && blob.size <= PHOTO_MAX) return { blob, mime: "image/jpeg" };
  const img = await createImageBitmap(blob);
  for (const side of [2560, 2048, 1600]) {
    const scale = Math.min(1, side / Math.max(img.width, img.height));
    const c = Object.assign(document.createElement("canvas"), { width: Math.round(img.width * scale), height: Math.round(img.height * scale) });
    c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
    const out = await new Promise<Blob | null>((r) => c.toBlob(r, "image/jpeg", 0.9));
    if (out && out.size <= PHOTO_MAX) return { blob: out, mime: "image/jpeg" };
  }
  throw new Error(`“${item.title}”: não consegui deixar a foto com menos de 5 MB.`);
}

export function SalesPage({ onFolders }: { onFolders: () => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [picking, setPicking] = useState<Product | null>(null);
  const [pixToken, setPixToken] = useState("");

  useEffect(() => {
    call()
      .then(setStatus)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function run(key: string, body: object, done = "") {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      setStatus(await call(body));
      if (done) setNotice(done);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(null);
    }
  }

  const botLink = status?.bot ? `https://t.me/${status.bot}` : null;

  return (
    <div className="st-sales">
      <div className="st-page-head">
        <div>
          <h1>Vendas</h1>
          <p>O bot de vendas que os fãs abrem no Telegram, os pacotes e quem comprou. Conteúdo só para maiores de 18.</p>
          <p className="st-hint">
            O conteúdo de cada pacote e do canal VIP você controla na aba{" "}
            <button type="button" className="st-link" onClick={onFolders}>
              Telegram
            </button>
            .
          </p>
        </div>
      </div>
      {error && <p className="st-error">{error}</p>}
      {notice && <p className="st-ok">{notice}</p>}
      {!status && !error && <div className="st-empty">Carregando...</div>}

      {status && (
        <>
          <section className="st-panel st-settings">
            <h2>Bot de vendas</h2>
            {status.connected ? (
              <p className="st-ok">
                Ligado{status.bot ? (
                  <>
                    {" "}
                    como{" "}
                    <a href={botLink!} target="_blank" rel="noreferrer">
                      @{status.bot}
                    </a>
                  </>
                ) : null}
                . Os fãs abrem esse bot, confirmam que têm 18 anos e escolhem o pacote.
              </p>
            ) : (
              <ol className="st-steps">
                <li>
                  No Telegram, abra o <strong>@BotFather</strong>, mande <code>/newbot</code> e crie um bot <strong>novo</strong> para os fãs
                  (não use o bot de aprovações).
                </li>
                <li>Cole o token abaixo, salve e toque em Ligar.</li>
              </ol>
            )}
            {!status.connected && (
              <div className="st-row">
                <input
                  type="password"
                  className="st-input"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder={status.tokenSet ? "Token salvo (cole outro para trocar)" : "123456789:ABC..."}
                  autoComplete="off"
                  aria-label="Token do bot de vendas"
                />
                <button type="button" className="st-btn" disabled={!token.trim() || busy !== null} onClick={async () => (await run("token", { action: "token", token: token.trim() }, "Token salvo.")) && setToken("")}>
                  {busy === "token" ? "Conferindo..." : "Salvar token"}
                </button>
                <button type="button" className="st-btn st-btn-primary" disabled={!status.tokenSet || busy !== null} onClick={() => run("connect", { action: "connect" }, "Bot de vendas ligado.")}>
                  {busy === "connect" ? "Ligando..." : "Ligar"}
                </button>
              </div>
            )}
            {status.connected && !status.adminStarted && (
              <p className="st-warn">
                Abra o bot de vendas no seu Telegram{botLink ? (
                  <>
                    {" "}
                    (
                    <a href={botLink} target="_blank" rel="noreferrer">
                      @{status.bot}
                    </a>
                    )
                  </>
                ) : null}{" "}
                e toque em <strong>Começar</strong>: é na conversa com você que ele guarda as fotos dos pacotes. Depois recarregue esta página.
              </p>
            )}
            {status.connected && (
              <div className="st-row">
                <button type="button" className="st-btn st-btn-small" disabled={busy !== null || !status.adminStarted} onClick={() => run("test", { action: "test" }, "Mandei o menu dos pacotes no seu Telegram, como o fã vê.")}>
                  Ver como o fã vê
                </button>
                <button
                  type="button"
                  className="st-btn st-btn-small"
                  disabled={busy !== null}
                  onClick={() => window.confirm("Desligar o bot de vendas? Os fãs param de ser atendidos.") && run("forget", { action: "forget" }, "Bot de vendas desligado.")}
                >
                  Desligar
                </button>
              </div>
            )}
          </section>

          <section className="st-panel st-settings">
            <h2>Fãs</h2>
            <div className="st-sales-stats">
              <span>
                <b>{status.fans.total}</b> abriram o bot
              </span>
              <span>
                <b>{status.fans.adults}</b> confirmaram 18+
              </span>
              <span>
                <b>{status.fans.today}</b> novos hoje
              </span>
              <span>
                <b>{status.fans.optedOut}</b> pararam as novidades
              </span>
              <span>
                <b>{status.fans.blocked}</b> bloquearam o bot
              </span>
            </div>
          </section>

          <section className="st-panel st-settings">
            <h2>Pix (PushinPay)</h2>
            {status.pix.configured ? (
              <>
                <p className="st-ok">
                  Pix ligado{status.pix.env === "sandbox" ? " em modo de teste (sandbox): nenhum pagamento é de verdade" : ""}. O fã recebe o Pix copia-e-cola e, quando o
                  pagamento cai, o bot entrega sozinho.
                </p>
                <div className="st-row">
                  <button
                    type="button"
                    className="st-btn st-btn-small"
                    disabled={busy !== null}
                    onClick={() => window.confirm("Desligar o Pix? Quem tocar num pacote volta a receber “quase pronto”.") && run("pix-forget", { action: "pix-forget" }, "Pix desligado.")}
                  >
                    Desligar o Pix
                  </button>
                </div>
              </>
            ) : (
              <p className="st-hint">
                Ainda não ligado: quem toca num pacote recebe “quase pronto” e você é avisado no seu Telegram. No painel da PushinPay, gere o token da API e cole aqui.
              </p>
            )}
            {!status.pix.configured && (
              <div className="st-row">
                <input
                  type="password"
                  className="st-input"
                  value={pixToken}
                  onChange={(e) => setPixToken(e.target.value)}
                  placeholder="Token da API da PushinPay"
                  autoComplete="off"
                  aria-label="Token da API da PushinPay"
                />
                <button
                  type="button"
                  className="st-btn st-btn-primary"
                  disabled={!pixToken.trim() || busy !== null}
                  onClick={async () => (await run("pix", { action: "pix", token: pixToken.trim(), env: "producao" }, "Pix ligado.")) && setPixToken("")}
                >
                  {busy === "pix" ? "Salvando..." : "Ligar o Pix"}
                </button>
              </div>
            )}
          </section>

          <CtaEditor cta={status.cta} previews={status.previews.length} busy={busy} run={run} onFolders={onFolders} />

          <div className="st-sales-products">
            {status.products.map((p) => (
              <ProductCard key={p.id} product={p} channel={status.channel} busy={busy} run={run} onPick={() => setPicking(p)} />
            ))}
          </div>

          <section className="st-panel st-settings">
            <h2>Vendas</h2>
            <div className="st-sales-stats">
              <span>
                <b>{status.sales.count30}</b> vendas em 30 dias
              </span>
              <span>
                <b>R$ {money(status.sales.total30)}</b> recebidos
              </span>
              <span>
                <b>{status.sales.pending}</b> Pix esperando pagamento
              </span>
            </div>
            {status.orders.length === 0 ? (
              <p className="st-hint">Nenhum pedido ainda.</p>
            ) : (
              <ul className="st-sales-orders">
                {status.orders.map((o) => (
                  <li key={o.id}>
                    <span>{new Date(o.paid_at ?? o.created_at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}</span>
                    <span>{o.fan}</span>
                    <span>{o.product}</span>
                    <b>R$ {money(o.amount_cents)}</b>
                    <span className={o.note ? "st-sales-warn" : ""} title={o.note ?? undefined}>
                      {ORDER_STATUS[o.status] ?? o.status}
                      {o.note ? " ⚠" : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}

      {picking && status && (
        <PackPicker
          product={picking}
          adminStarted={status.adminStarted}
          onClose={() => setPicking(null)}
          onDone={(s, n) => {
            setStatus(s);
            setNotice(`${n} arquivo(s) guardado(s) no pacote “${picking.title}”.`);
            setPicking(null);
          }}
        />
      )}
    </div>
  );
}

// Mensagens que chamam o fa para comprar: boas-vindas, Pix esquecido e a diaria.
function CtaEditor({
  cta,
  previews,
  busy,
  run,
  onFolders,
}: {
  cta: Cta;
  previews: number;
  busy: string | null;
  run: (key: string, body: object, done?: string) => Promise<boolean>;
  onFolders: () => void;
}) {
  const [draft, setDraft] = useState<Cta>(cta);
  useEffect(() => setDraft(cta), [cta]);
  const changed = JSON.stringify(draft) !== JSON.stringify(cta);
  const set = (patch: Partial<Cta>) => setDraft((d) => ({ ...d, ...patch }));
  return (
    <section className="st-panel st-settings st-cta">
      <h2>Mensagens para os fãs (CTA)</h2>
      <p className="st-hint">
        <b>{"{nome}"}</b> vira o primeiro nome do fã. Cada mensagem vai com uma prévia ({previews} na aba{" "}
        <button type="button" className="st-link" onClick={onFolders}>
          Telegram → Prévias
        </button>
        ) e os botões dos pacotes. Nas mensagens grátis, provoque sem descrever sexo: quem recebe ainda não pagou.
      </p>
      <label className="st-field">
        <span>Boas-vindas (logo depois do “tenho 18”)</span>
        <textarea className="st-input" rows={3} maxLength={900} value={draft.welcome} onChange={(e) => set({ welcome: e.target.value })} />
      </label>
      <label className="st-field">
        <span>Pix esquecido (20 min depois, se não pagou). {"{pacote}"} vira o nome do pacote.</span>
        <textarea className="st-input" rows={3} maxLength={900} value={draft.pixReminder} onChange={(e) => set({ pixReminder: e.target.value })} />
      </label>
      <div className="st-row st-row-tight">
        <label className="st-check">
          <input type="checkbox" checked={draft.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
          Mensagem diária ligada
        </label>
        {draft.hours.map((hour, i) => (
          <label key={i} className="st-field st-cta-hour">
            <span>{i === 0 ? "às" : "e às"}</span>
            <select
              className="st-input"
              value={hour}
              onChange={(e) => set({ hours: draft.hours.map((h, j) => (j === i ? Number(e.target.value) : h)) })}
            >
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>
                  {String(h).padStart(2, "0")}:00
                </option>
              ))}
            </select>
          </label>
        ))}
        {draft.hours.length > 1 && (
          <button type="button" className="st-btn st-btn-small" onClick={() => set({ hours: draft.hours.slice(0, -1) })}>
            − horário
          </button>
        )}
        {draft.hours.length < 4 && (
          <button type="button" className="st-btn st-btn-small" onClick={() => set({ hours: [...draft.hours, Math.min(23, (draft.hours.at(-1) ?? 18) + 3)] })}>
            + horário
          </button>
        )}
      </div>
      <p className="st-hint">
        Uma em cada horário para quem ainda não é VIP (para com /parar). Cada fã recebe as frases na ordem abaixo (1, 2, 3…) e, depois da última, volta para a primeira; a prévia pode repetir.
      </p>
      <PhraseList label="Frase" items={draft.daily} onChange={(daily) => set({ daily })} />
      <div className="st-row st-row-tight">
        <button type="button" className="st-btn st-btn-small" onClick={() => set({ daily: [...draft.daily, "Oi {nome}… "] })}>
          <Icon name="plus" size={14} />
          Adicionar frase
        </button>
      </div>

      <h3>Renovação do VIP</h3>
      <p className="st-hint">
        De 3 dias antes do vencimento até sair do canal (3 dias depois), uma mensagem por dia com uma prévia e o botão “Renovar agora”; no primeiro aviso o Pix
        já vai junto. <b>{"{data}"}</b> vira a data do vencimento e <b>{"{dias}"}</b> quanto falta.
      </p>
      <PhraseList label="Aviso" items={draft.renewal} onChange={(renewal) => set({ renewal })} />
      <div className="st-row st-row-tight">
        <button type="button" className="st-btn st-btn-small" onClick={() => set({ renewal: [...draft.renewal, "{nome}, seu VIP vence em {data}… "] })}>
          <Icon name="plus" size={14} />
          Adicionar aviso
        </button>
      </div>
      <label className="st-field">
        <span>Depois que venceu (ainda no canal). {"{data}"} vira o dia em que ele sai.</span>
        <textarea className="st-input" rows={2} maxLength={900} value={draft.renewalGrace} onChange={(e) => set({ renewalGrace: e.target.value })} />
      </label>

      <div className="st-row">
        <button
          type="button"
          className="st-btn st-btn-small st-btn-primary"
          disabled={!changed || busy !== null}
          onClick={() =>
            run("cta-save", { action: "cta-save", ...draft, daily: draft.daily.filter((t) => t.trim()), renewal: draft.renewal.filter((t) => t.trim()) }, "Mensagens salvas.")
          }
        >
          Salvar mensagens
        </button>
        <button
          type="button"
          className="st-btn st-btn-small"
          disabled={busy !== null || changed}
          title={changed ? "Salve antes de testar" : undefined}
          onClick={() => run("cta-test", { action: "cta-test" }, "Mandei a mensagem de hoje no seu Telegram, como o fã recebe.")}
        >
          Mandar um teste para mim
        </button>
      </div>
    </section>
  );
}

// Lista de frases editaveis (uma caixa por frase, com o x para tirar).
function PhraseList({ label, items, onChange }: { label: string; items: string[]; onChange: (items: string[]) => void }) {
  return (
    <ol className="st-cta-list">
      {items.map((text, i) => (
        <li key={i}>
          <textarea
            className="st-input"
            rows={2}
            maxLength={900}
            value={text}
            aria-label={`${label} ${i + 1}`}
            onChange={(e) => onChange(items.map((t, j) => (j === i ? e.target.value : t)))}
          />
          <button
            type="button"
            className="st-icon-btn"
            aria-label={`Tirar ${label.toLowerCase()} ${i + 1}`}
            disabled={items.length <= 1}
            onClick={() => onChange(items.filter((_, j) => j !== i))}
          >
            <Icon name="close" size={14} />
          </button>
        </li>
      ))}
    </ol>
  );
}

function ProductCard({
  product: p,
  channel,
  busy,
  run,
  onPick,
}: {
  product: Product;
  channel: string | null;
  busy: string | null;
  run: (key: string, body: object, done?: string) => Promise<boolean>;
  onPick: () => void;
}) {
  const { items } = useStudio();
  const [title, setTitle] = useState(p.title);
  const [price, setPrice] = useState(money(p.price_cents));
  const [days, setDays] = useState(String(p.period_days));
  const [description, setDescription] = useState(p.description);
  const byId = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);
  const parsed = cents(price);
  const changed = title !== p.title || parsed !== p.price_cents || description !== p.description || Number(days) !== p.period_days;
  const ready = p.kind === "vip" ? Boolean(channel) : p.media.length > 0;

  return (
    <section className="st-panel st-sales-product">
      <div className="st-sales-product-head">
        <span className="st-hint">{kindLabel(p)}</span>
        <label className="st-check">
          <input type="checkbox" checked={p.active} disabled={busy !== null} onChange={(e) => run(`active-${p.id}`, { action: "product", id: p.id, active: e.target.checked })} />
          À venda
        </label>
      </div>
      <label className="st-field">
        <span>Nome</span>
        <input className="st-input" value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <div className="st-sales-pair">
        <label className="st-field">
          <span>Preço (R$)</span>
          <input className="st-input" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
        </label>
        <label className="st-field">
          <span>{p.billing === "mensal" ? "Dias por mensalidade" : "Dias de acesso"}</span>
          <input className="st-input" type="number" min={1} max={366} value={days} onChange={(e) => setDays(e.target.value)} />
        </label>
      </div>
      <label className="st-field">
        <span>Descrição</span>
        <textarea className="st-input" rows={2} maxLength={600} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      {changed && (
        <button
          type="button"
          className="st-btn st-btn-small st-btn-primary"
          disabled={busy !== null || parsed === null}
          onClick={() =>
            run(`save-${p.id}`, { action: "product", id: p.id, title, description, price_cents: parsed, period_days: Number(days) }, "Pacote salvo.")
          }
        >
          Salvar
        </button>
      )}

      {p.kind === "vip" ? (
        <p className={channel ? "st-hint" : "st-warn"}>
          {channel
            ? `Acesso ao canal “${channel}”, que recebe sozinho o conteúdo +18 que você aprova.${
                p.billing === "mensal" ? " Renova sozinho todo mês pelo Pix; o fã cancela com /cancelar." : " Vencido, o fã sai do canal e recebe o botão de renovar."
              }`
            : "Conecte o canal +18 em Meu perfil → Canal +18: o VIP é o acesso a ele."}
        </p>
      ) : (
        <>
          <p className="st-hint">
            {p.media.length ? `${p.media.length} foto(s)/vídeo(s) no pacote.` : "Pacote vazio: não aparece para os fãs até ter conteúdo."} Depois de pago, o fã
            pode abrir de novo pelo bot durante os dias de acesso.
          </p>
          {p.media.length > 0 && (
            <div className="st-sales-media">
              {p.media.map((m) => {
                const item = byId.get(m.media_id);
                return (
                  <div key={m.media_id} className="st-pick">
                    {item ? <Thumb item={item} /> : <div className="st-thumb st-sales-missing">{m.kind === "video" ? "vídeo" : "foto"}</div>}
                    <button
                      type="button"
                      className="st-icon-btn st-sales-remove"
                      aria-label="Tirar do pacote"
                      disabled={busy !== null}
                      onClick={() => run(`rm-${m.media_id}`, { action: "media-remove", product_id: p.id, media_id: m.media_id })}
                    >
                      <Icon name="close" size={14} />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
          <button type="button" className="st-btn st-btn-small" disabled={busy !== null} onClick={onPick}>
            <Icon name="plus" size={14} />
            Colocar fotos e vídeos
          </button>
        </>
      )}
      {!ready && p.active && <p className="st-hint">Ainda não aparece no menu dos fãs.</p>}
    </section>
  );
}

function PackPicker({ product, adminStarted, onClose, onDone }: { product: Product; adminStarted: boolean; onClose: () => void; onDone: (s: Status, n: number) => void }) {
  const { items } = useStudio();
  const [onlyAdult, setOnlyAdult] = useState(true);
  const [picked, setPicked] = useState<string[]>([]);
  const [step, setStep] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inPack = new Set(product.media.map((m) => m.media_id));
  // so o que ja esta cadastrado no banco (tem id) pode entrar num pacote
  const library = items.filter((i) => i.id && !inPack.has(i.id) && i.display !== "arquivado" && (!onlyAdult || i.rating !== "livre"));

  async function save() {
    setError(null);
    let status: Status | null = null;
    let n = 0;
    try {
      for (const [i, path] of picked.entries()) {
        const item = items.find((x) => x.path === path)!;
        setStep(`Guardando ${i + 1} de ${picked.length}...`);
        const { blob, mime } = await prepare(item);
        const up = await call({ action: "media-url", product_id: product.id, mime_type: mime, size_bytes: blob.size });
        await putSignedUpload(up.url!, blob);
        status = await call({ action: "media-add", product_id: product.id, media_id: item.id, path: up.path });
        n++;
      }
      onDone(status!, n);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStep(null);
    }
  }

  return (
    <Modal title={`Conteúdo do pacote “${product.title}”`} onClose={onClose} wide>
      {!adminStarted && <p className="st-warn">Antes, abra o bot de vendas no seu Telegram e toque em Começar.</p>}
      <label className="st-check">
        <input type="checkbox" checked={onlyAdult} onChange={(e) => setOnlyAdult(e.target.checked)} />
        Só fotos e vídeos marcados como +18 ou sensíveis
      </label>
      {library.length === 0 ? (
        <p className="st-hint">Nada para escolher na biblioteca{onlyAdult ? " (tire o filtro para ver tudo)" : ""}.</p>
      ) : (
        <div className="st-picker">
          {library.map((item) => {
            const n = picked.indexOf(item.path);
            return (
              <div key={item.path} className={n >= 0 ? "st-pick on" : "st-pick"}>
                <Thumb item={item} onClick={() => setPicked((p) => (n >= 0 ? p.filter((x) => x !== item.path) : [...p, item.path]))} />
                {n >= 0 && <span className="st-pick-n">{n + 1}</span>}
                <span className="st-pick-name">{item.title}</span>
              </div>
            );
          })}
        </div>
      )}
      {error && <p className="st-error">{error}</p>}
      <div className="st-composer-actions">
        <span className="st-hint">{step ?? `${picked.length} escolhido(s)`}</span>
        <button type="button" className="st-btn st-btn-primary" disabled={!picked.length || step !== null || !adminStarted} onClick={save}>
          Guardar no pacote
        </button>
      </div>
    </Modal>
  );
}
