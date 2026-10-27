import { useEffect, useMemo, useState } from "react";
import { putSignedUpload } from "./cloud";
import { Icon } from "./Icon";
import { Modal, Thumb } from "./shared";
import { useStudio, type Item } from "./store";

// Vendas (+18): o bot de vendas que os fas abrem no Telegram, os pacotes
// (ensaio, album e VIP por assinatura) com o conteudo de cada um e os
// numeros de fas e pedidos. As fotos dos pacotes saem da pasta do PC, sobem
// uma vez para o Telegram (o estudio guarda so o codigo do arquivo) e a copia
// temporaria e apagada.

interface Product {
  id: string;
  kind: "ensaio" | "album" | "vip";
  title: string;
  description: string;
  price_cents: number;
  period_days: number | null;
  active: boolean;
  media: { media_id: string; kind: "photo" | "video"; path: string | null }[];
}
interface Status {
  storeReady: boolean;
  tokenSet: boolean;
  bot: string | null;
  connected: boolean;
  adminStarted: boolean;
  channel: string | null;
  products: Product[];
  fans: { total: number; adults: number; optedOut: number; blocked: number; today: number };
  orders: { id: string; status: string; amount_cents: number; created_at: string; product_id: string }[];
}

const KIND: Record<Product["kind"], string> = { ensaio: "Ensaio", album: "Álbum", vip: "VIP (assinatura)" };
const PHOTO_MAX = 5 * 1024 * 1024;
const VIDEO_MAX = 20 * 1024 * 1024;

async function call(body?: object): Promise<Status & { url?: string; path?: string }> {
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
async function prepare(item: Item): Promise<{ blob: Blob; mime: string }> {
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

export function SalesPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [picking, setPicking] = useState<Product | null>(null);

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
            <p className="st-hint">
              Pagamento por Pix: ainda não ligado (falta o gateway). Quem toca num pacote recebe “quase pronto” e você é avisado no seu Telegram.
            </p>
          </section>

          <div className="st-sales-products">
            {status.products.map((p) => (
              <ProductCard key={p.id} product={p} channel={status.channel} busy={busy} run={run} onPick={() => setPicking(p)} />
            ))}
          </div>
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
  const [days, setDays] = useState(String(p.period_days ?? 30));
  const [description, setDescription] = useState(p.description);
  const byId = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);
  const parsed = cents(price);
  const changed = title !== p.title || parsed !== p.price_cents || description !== p.description || (p.kind === "vip" && Number(days) !== p.period_days);
  const ready = p.kind === "vip" ? Boolean(channel) : p.media.length > 0;

  return (
    <section className="st-panel st-sales-product">
      <div className="st-sales-product-head">
        <span className="st-hint">{KIND[p.kind]}</span>
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
        {p.kind === "vip" && (
          <label className="st-field">
            <span>Dias de acesso</span>
            <input className="st-input" type="number" min={1} max={366} value={days} onChange={(e) => setDays(e.target.value)} />
          </label>
        )}
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
            run(`save-${p.id}`, { action: "product", id: p.id, title, description, price_cents: parsed, ...(p.kind === "vip" ? { period_days: Number(days) } : {}) }, "Pacote salvo.")
          }
        >
          Salvar
        </button>
      )}

      {p.kind === "vip" ? (
        <p className={channel ? "st-hint" : "st-warn"}>
          {channel
            ? `Acesso ao canal “${channel}”, que recebe sozinho o conteúdo +18 que você aprova.`
            : "Conecte o canal +18 em Meu perfil → Canal +18: o VIP é o acesso a ele."}
        </p>
      ) : (
        <>
          <p className="st-hint">{p.media.length ? `${p.media.length} foto(s)/vídeo(s) no pacote.` : "Pacote vazio: não aparece para os fãs até ter conteúdo."}</p>
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
