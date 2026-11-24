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
  // 23/11: fa de fora recebe em ingles; 1 por dia; para depois de N dias sem tocar
  welcomeEn: string;
  dailyEn: string[];
  titlesEn: Record<string, string>;
  oncePerDay: boolean;
  stopAfterDays: number;
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
  // estudo do publico (23/11): lingua do app do Telegram de cada fa novo e Brasil/fora
  study?: { langs: { code: string; fans: number }[]; brasil: number; fora: number } | null;
  // meta do ano (23/11): R$ 10 mil de 01/11 a 31/12, Luna + Cleuza
  goal?: { target: number; cents: number; sales: number; daysLeft: number } | null;
  pix: { configured: boolean; env: "producao" | "sandbox" };
  sales: { count30: number; total30: number; pending: number; starsCount30?: number; stars30?: number; intlCount30?: number; intl30?: number };
  vipPosts: { media_id: string; path: string | null }[]; // arquivos da pasta do VIP ja postados no canal
  // publicacoes do Chat +18: entram nos pacotes ao aprovar e no canal no horario
  chat18?: { post_id: string; status: string; scheduled_at: string | null; channel: boolean; packs: string[]; media: { media_id: string; path: string | null }[] }[];
  previews: { media_id: string; kind: "photo" | "video"; path: string | null; text: string }[]; // previas das mensagens (CTA) e a chamada de cada uma
  // arquivos novos das pastas esperando a aprovacao no Telegram, e os recusados la
  folderPending?: { media_id: string; target: "pack" | "vip" | "previa"; product_id: string | null; text?: string }[];
  folderRejected?: { media_id: string; target: "pack" | "vip" | "previa" }[];
  cta: Cta;
  // gateway "estrelas": amount_cents sao estrelas do Telegram, nao centavos
  orders: { id: string; status: string; amount_cents: number; created_at: string; paid_at: string | null; access_until: string | null; product: string; fan: string; fan_id?: string; note: string | null; gateway?: string | null }[];
  vipMembers: { fan: string; until: string }[]; // quem esta no canal VIP agora (vencido ha ate 3 dias ainda conta)
  channelMembers: number | null; // o Telegram conta os membros do canal VIP (com os administradores)
  promo: Promo;
  stars?: { enabled: boolean; prices: Record<string, number>; suggested: Record<string, number> }; // estrelas do Telegram (fora do Brasil)
  // pagamento internacional (NOWPayments): precos em centavos de dolar; as chaves nunca vem
  intl?: { enabled: boolean; configured: boolean; apiKeySet: boolean; ipnSecretSet: boolean; prices: Record<string, number>; suggested: Record<string, number> };
  // chamada para quem gerou o pagamento e nao pagou (22/11)
  chamada?: Chamada | null;
}
interface Chamada {
  id: string;
  status: "pronta" | "enviando" | "feita" | "cancelada";
  created_at: string;
  total: number;
  done: number;
  sent: number;
  blocked: number;
  failed: number;
  error: string | null;
  example: { pt: string; en: string } | null;
}

// lingua do app do Telegram (language_code) -> nome
const LANG_NAMES: Record<string, string> = {
  pt: "português", en: "inglês", es: "espanhol", fa: "persa (Irã)", ar: "árabe", hi: "hindi (Índia)", ur: "urdu (Paquistão)", uz: "uzbeque",
  ru: "russo", tr: "turco", id: "indonésio", de: "alemão", fr: "francês", it: "italiano", bn: "bengali", ms: "malaio", uk: "ucraniano",
  kk: "cazaque", az: "azerbaijano", he: "hebraico", nl: "holandês", pl: "polonês", ko: "coreano", ja: "japonês", zh: "chinês", th: "tailandês", vi: "vietnamita",
};

const CHAMADA_STATUS: Record<Chamada["status"], string> = {
  pronta: "esperando você enviar",
  enviando: "enviando aos poucos",
  feita: "enviada",
  cancelada: "cancelada",
};

function ChamadaPanel({ chamada, busy, run }: { chamada: Chamada | null | undefined; busy: string | null; run: (key: string, body: object, done?: string) => Promise<boolean> }) {
  const c = chamada ?? null;
  return (
    <section className="st-panel st-settings">
      <h2>Chamar quem não pagou</h2>
      <p className="st-hint">
        Uma mensagem para quem gerou o pagamento nos últimos 7 dias e não pagou (+18, sem bloqueio, sem compra): prévia borrada, texto da IA na língua
        de cada um (de fora: inglês; Brasil: português; sem saber: as duas) e os botões do pacote que escolheu (cartão, cripto e Pix). No dia da
        promoção os preços e a faixa 🔥 vão junto, e no 1º dia dela o seu Telegram pergunta se quer mandar. Cada fã recebe uma vez. Antes, o exemplo
        chega no seu Telegram.
      </p>
      {c && (
        <p className={c.status === "cancelada" ? "st-hint" : "st-ok"}>
          Última: {new Date(c.created_at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })} · {c.total} fã(s) · {CHAMADA_STATUS[c.status]}
          {c.status !== "pronta" ? ` · ${c.sent} receberam${c.blocked ? `, ${c.blocked} tinham bloqueado` : ""}${c.failed ? `, ${c.failed} falharam` : ""}` : ""}
          {c.status === "enviando" ? ` (${c.done}/${c.total})` : ""}
        </p>
      )}
      {c?.example && c.status === "pronta" && <p className="st-hint">Exemplo: “{c.example.pt}”</p>}
      {c?.error && <p className="st-sales-warn">{c.error}</p>}
      <div className="st-row">
        <button
          type="button"
          className="st-btn"
          disabled={busy !== null || c?.status === "enviando"}
          onClick={() => run("chamada-prepare", { action: "chamada-prepare" }, "Chamada preparada: o exemplo chegou no seu Telegram.")}
        >
          {busy === "chamada-prepare" ? "Preparando (a IA está escrevendo)..." : "Preparar chamada"}
        </button>
        {c?.status === "pronta" && c.total > 0 && (
          <button type="button" className="st-btn st-btn-primary" disabled={busy !== null} onClick={() => run("chamada-send", { action: "chamada-send", id: c.id }, "Enviando aos poucos. O resultado chega no seu Telegram.")}>
            {busy === "chamada-send" ? "Ligando..." : `Enviar para ${c.total} fã(s)`}
          </button>
        )}
        {(c?.status === "pronta" || c?.status === "enviando") && (
          <button type="button" className="st-btn" disabled={busy !== null} onClick={() => run("chamada-cancel", { action: "chamada-cancel", id: c.id }, "Chamada cancelada.")}>
            Cancelar
          </button>
        )}
      </div>
    </section>
  );
}
export interface Promo {
  enabled: boolean;
  days: number[]; // 0 = domingo
  label: string;
  labelEn?: string; // nome para o fa de fora (23/11)
  prices: Record<string, number>;
  stars?: Record<string, number>; // promocao em estrelas
  usd?: Record<string, number>; // promocao em centavos de dolar
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
            <h3>De onde vem quem entra (desde 23/11)</h3>
            {status.study && (status.study.langs.length > 0 || status.study.brasil + status.study.fora > 0) ? (
              <>
                <p className="st-hint">
                  Na idade: <b>{status.study.brasil}</b> disseram “sou do Brasil” e <b>{status.study.fora}</b> “fora do Brasil”. Língua do app do Telegram (cada fã conta
                  uma vez):
                </p>
                <div className="st-sales-stats">
                  {status.study.langs.map((l) => (
                    <span key={l.code}>
                      <b>{l.fans}</b> {LANG_NAMES[l.code] ?? l.code}
                    </span>
                  ))}
                </div>
              </>
            ) : (
              <p className="st-hint">Ainda sem dados: a contagem começa com os fãs que tocarem no bot a partir de agora.</p>
            )}
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

          <CtaEditor cta={status.cta} products={status.products.filter((p) => p.active)} previews={status.previews.length} busy={busy} run={run} onFolders={onFolders} />

          {status.connected && <ChamadaPanel chamada={status.chamada} busy={busy} run={run} />}

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
              {status.intl?.enabled || status.sales.intlCount30 ? (
                <span>
                  <b>US$ {((status.sales.intl30 ?? 0) / 100).toFixed(2)}</b> internacional ({status.sales.intlCount30 ?? 0} venda(s))
                </span>
              ) : null}
              {status.stars?.enabled || status.sales.starsCount30 ? (
                <span>
                  <b>⭐ {status.sales.stars30 ?? 0}</b> em estrelas ({status.sales.starsCount30 ?? 0} venda(s), fora do Brasil)
                </span>
              ) : null}
            </div>
            {status.orders.length === 0 ? (
              <p className="st-hint">Nenhum pedido ainda.</p>
            ) : (
              <ul className="st-sales-orders">
                {status.orders.map((o, i) => (
                  <li key={o.id}>
                    <span>{new Date(o.paid_at ?? o.created_at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}</span>
                    <span>
                      {o.fan}
                      {o.status === "pendente" && o.fan_id && status.orders.findIndex((x) => x.fan_id === o.fan_id && x.status === "pendente") === i && (
                        <>
                          {" "}
                          <button
                            type="button"
                            className="st-btn st-btn-small"
                            disabled={busy !== null}
                            title="Manda para o fã um guia de como pagar (estrelas com cartão, cripto em USDT/Litecoin) com os botões do pacote"
                            onClick={() => window.confirm(`Mandar para ${o.fan} o guia de como pagar, com os botões do pacote?`) && run(`help-${o.fan_id}`, { action: "fan-help", fan_id: o.fan_id }, `🆘 Mandei o guia de pagamento para ${o.fan}.`)}
                          >
                            {busy === `help-${o.fan_id}` ? "Mandando..." : "🆘 Ajudar a pagar"}
                          </button>
                        </>
                      )}
                    </span>
                    <span>{o.product}</span>
                    <b>{o.gateway === "estrelas" ? `⭐ ${o.amount_cents}` : o.gateway === "nowpayments" ? `US$ ${(o.amount_cents / 100).toFixed(2)}` : `R$ ${money(o.amount_cents)}`}</b>
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
  products,
  previews,
  busy,
  run,
  onFolders,
}: {
  cta: Cta;
  products: Product[];
  previews: number;
  busy: string | null;
  run: (key: string, body: object, done?: string) => Promise<boolean>;
  onFolders: () => void;
}) {
  const [draft, setDraft] = useState<Cta>(cta);
  const [translating, setTranslating] = useState(false);
  const [translateMsg, setTranslateMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => setDraft(cta), [cta]);
  const changed = JSON.stringify(draft) !== JSON.stringify(cta);
  const set = (patch: Partial<Cta>) => setDraft((d) => ({ ...d, ...patch }));
  // a IA traduz as mensagens em portugues (as salvas); o resultado entra no rascunho para conferir
  async function translate() {
    setTranslating(true);
    setTranslateMsg(null);
    try {
      const r = (await call({ action: "cta-translate" })) as unknown as { translated?: { welcomeEn: string; dailyEn: string[]; titlesEn: Record<string, string> } };
      const t = r.translated;
      if (!t) throw new Error("A IA não devolveu a tradução.");
      setDraft((d) => ({
        ...d,
        welcomeEn: t.welcomeEn || d.welcomeEn,
        dailyEn: t.dailyEn.length ? t.dailyEn : d.dailyEn,
        titlesEn: { ...d.titlesEn, ...t.titlesEn },
      }));
      setTranslateMsg({ ok: true, text: `Traduzido: confira as frases em inglês abaixo e toque em “Salvar mensagens”. (${t.dailyEn.length} frase(s))` });
    } catch (e) {
      setTranslateMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setTranslating(false);
    }
  }
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
      <div className="st-cta-media">
        <div>
          <b>📣 Fotos e vídeos da chamada: {previews}</b>
          <span className="st-hint">
            Adicione aqui (o robô do PC borra as partes íntimas sozinho e a versão borrada chega no Telegram para você aprovar) ou toque em “📣 Usar na chamada (borrar)” numa foto +18 no Telegram.
          </span>
        </div>
        <button type="button" className="st-btn st-btn-primary st-btn-small" onClick={onFolders}>
          Adicionar foto ou vídeo (com borrão)
        </button>
      </div>
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
      <div className="st-row st-row-tight">
        <label className="st-check">
          <input type="checkbox" checked={draft.oncePerDay} onChange={(e) => set({ oncePerDay: e.target.checked })} />
          Só 1 mensagem por dia (os horários viram tentativas)
        </label>
        <label className="st-field st-cta-hour">
          <span>Parar depois de</span>
          <select className="st-input" value={draft.stopAfterDays} onChange={(e) => set({ stopAfterDays: Number(e.target.value) })}>
            {[0, 3, 5, 7, 10, 14, 21, 30].map((d) => (
              <option key={d} value={d}>
                {d === 0 ? "nunca" : `${d} dias`}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="st-hint">
        {draft.oncePerDay ? "No máximo uma por dia (a boas-vindas conta) " : "Uma em cada horário "}
        para quem ainda não é VIP (para com /parar){draft.stopAfterDays ? `; quem não toca em nada no bot há ${draft.stopAfterDays} dias para de receber e volta quando tocar` : ""}.
        Cada fã recebe as frases na ordem abaixo (1, 2, 3…) e, depois da última, volta para a primeira; a prévia pode repetir.
      </p>
      <PhraseList label="Frase" items={draft.daily} onChange={(daily) => set({ daily })} />
      <div className="st-row st-row-tight">
        <button type="button" className="st-btn st-btn-small" onClick={() => set({ daily: [...draft.daily, "Oi {nome}… "] })}>
          <Icon name="plus" size={14} />
          Adicionar frase
        </button>
      </div>

      <h3>🌎 Em inglês (fã de fora do Brasil)</h3>
      <p className="st-hint">
        Na confirmação da idade o fã escolhe “sou do Brasil” ou “fora do Brasil”. Quem é de fora recebe tudo em inglês e os pacotes com o preço em estrelas
        (cartão, Apple Pay, Google Pay) ou em dólar; quem ainda não escolheu (fãs antigos) recebe pela língua do Telegram ou, sem saber, as duas línguas.
      </p>
      <div className="st-row st-row-tight">
        <button type="button" className="st-btn st-btn-small" disabled={translating || busy !== null || changed} title={changed ? "Salve antes de traduzir" : undefined} onClick={translate}>
          {translating ? "Traduzindo (a IA está escrevendo)…" : "✨ Traduzir as mensagens com a IA"}
        </button>
      </div>
      {translateMsg && <p className={translateMsg.ok ? "st-ok" : "st-error"}>{translateMsg.text}</p>}
      <label className="st-field">
        <span>Boas-vindas em inglês</span>
        <textarea className="st-input" rows={3} maxLength={900} value={draft.welcomeEn} onChange={(e) => set({ welcomeEn: e.target.value })} />
      </label>
      <PhraseList label="Frase em inglês" items={draft.dailyEn} onChange={(dailyEn) => set({ dailyEn })} />
      <div className="st-row st-row-tight">
        <button type="button" className="st-btn st-btn-small" onClick={() => set({ dailyEn: [...draft.dailyEn, "Hey {nome}… "] })}>
          <Icon name="plus" size={14} />
          Adicionar frase em inglês
        </button>
      </div>
      {products.length > 0 && (
        <div className="st-plan-rows">
          {products.map((p) => (
            <label key={p.id} className="st-field">
              <span>Nome em inglês de “{p.title}” (vazio: nome padrão)</span>
              <input
                className="st-input"
                maxLength={60}
                value={draft.titlesEn[p.id] ?? ""}
                placeholder={p.kind === "vip" ? "Full VIP: videos + photos every day" : p.kind === "album" ? "Uncensored VIP album" : "Photo set"}
                onChange={(e) => set({ titlesEn: { ...draft.titlesEn, [p.id]: e.target.value } })}
              />
            </label>
          ))}
        </div>
      )}

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
            run(
              "cta-save",
              {
                action: "cta-save",
                ...draft,
                daily: draft.daily.filter((t) => t.trim()),
                dailyEn: draft.dailyEn.filter((t) => t.trim()),
                renewal: draft.renewal.filter((t) => t.trim()),
                titlesEn: Object.fromEntries(Object.entries(draft.titlesEn).filter(([, t]) => t.trim())),
              },
              "Mensagens salvas."
            )
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
        <button
          type="button"
          className="st-btn st-btn-small"
          disabled={busy !== null || changed}
          title={changed ? "Salve antes de testar" : undefined}
          onClick={() => run("cta-test-en", { action: "cta-test", lang: "en" }, "Mandei no seu Telegram a mensagem como o fã de fora recebe (em inglês).")}
        >
          Teste em inglês
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
      {ready && (
        <button
          type="button"
          className="st-btn st-btn-small"
          disabled={busy !== null}
          title="Cortesia para você: chega no seu Telegram igual a uma compra, sem Pix e sem contar como venda"
          onClick={() =>
            window.confirm(`Liberar “${p.title}” para você, de cortesia? Chega no seu Telegram igual a um fã que pagou (sem Pix, não conta como venda).`) &&
            run(
              `gift-${p.id}`,
              { action: "gift", product_id: p.id },
              p.kind === "vip"
                ? `🎁 Liberei “${p.title}” para você: o convite do canal chegou no seu Telegram, na conversa com o bot de vendas.`
                : `🎁 Liberei “${p.title}” para você: chegou no seu Telegram e fica em 📂 Meus pacotes.`
            )
          }
        >
          🎁 Liberar pra mim
        </button>
      )}
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
