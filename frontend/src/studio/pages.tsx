import { WorkerPcPanel } from "./workerpc";
import { useEffect, useState } from "react";
import { Calendar } from "./Calendar";
import { Composer } from "./Composer";
import { POST_STATUS, PostStatusBadge } from "./PostFlow";
import { Icon } from "./Icon";
import { cloudAudit } from "./cloud";
import { FolderGate, Modal, NETWORKS, WhenReady, StatusBadge, Thumb, formatBytes, formatDate } from "./shared";
import { metaProblem, useStudio, type Audience, type Campaign, type Character, type Item, type ItemStatus, type MetaPatch, type Network, type Post, type PostStatus } from "./store";

export interface Nav {
  openItem: (path: string) => void;
  openComposer: (opts: { post?: Post; date?: Date; media?: string[] }) => void;
  go: (page: string) => void;
  pickFiles: () => void;
}

type Filter = "todos" | "imagens" | "videos" | "aprovados" | "rascunhos" | "agendados" | "arquivados";

const FILTER_LABEL: Record<Filter, string> = {
  todos: "Todos",
  imagens: "Imagens",
  videos: "Vídeos",
  aprovados: "Aprovados",
  rascunhos: "Rascunhos",
  agendados: "Agendados",
  arquivados: "Arquivados",
};

interface Extra {
  characterId?: string;
  campaignId?: string;
  audience?: string;
}

function applyFilter(items: Item[], filter: Filter, query = "", extra: Extra = {}) {
  const q = query.trim().toLowerCase();
  return items.filter((i) => {
    if (extra.characterId && i.characterId !== extra.characterId) return false;
    if (extra.campaignId && i.campaignId !== extra.campaignId) return false;
    if (extra.audience && (i.audience ?? "promocional") !== extra.audience) return false;
    if (filter === "arquivados" ? i.display !== "arquivado" : i.display === "arquivado") return false;
    if (filter === "imagens" && i.kind !== "image") return false;
    if (filter === "videos" && i.kind !== "video") return false;
    if (filter === "aprovados" && i.display !== "aprovado") return false;
    if (filter === "rascunhos" && i.display !== "rascunho") return false;
    if (filter === "agendados" && i.display !== "agendado") return false;
    if (!q) return true;
    const hay = [i.title, i.path, i.description, i.caption, i.category, ...(i.hashtags ?? [])].join(" ").toLowerCase();
    return hay.includes(q);
  });
}

function FilterTabs({ value, options, onChange }: { value: Filter; options: Filter[]; onChange: (f: Filter) => void }) {
  return (
    <div className="st-tabs">
      {options.map((f) => (
        <button key={f} type="button" className={value === f ? "active" : ""} onClick={() => onChange(f)}>
          {FILTER_LABEL[f]}
        </button>
      ))}
    </div>
  );
}

function ContentCard({ item, onOpen }: { item: Item; onOpen: () => void }) {
  return (
    <div className="st-card">
      <Thumb item={item} onClick={onOpen} />
      <div className="st-card-title" title={item.title}>
        {item.title}
      </div>
      <div className="st-card-meta">
        <span>{formatDate(item.addedAt)}</span>
        <span className="st-card-flags">
          {item.rating === "adulto" && <span className="st-flag st-flag-adult" title="Conteúdo adulto: só Telegram">+18</span>}
          {item.audience === "assinantes" && <span className="st-flag" title="Exclusivo para assinantes">VIP</span>}
          <StatusBadge status={item.display} />
        </span>
      </div>
    </div>
  );
}

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`;
}

// --- Visao geral ------------------------------------------------------------------

function Stat({ icon, tone, label, value, sub }: { icon: string; tone: string; label: string; value: number | string; sub: string }) {
  return (
    <div className="st-panel st-stat">
      <span className={`st-stat-icon st-tone-${tone}`}>
        <Icon name={icon} size={22} />
      </span>
      <div>
        <div className="st-stat-label">{label}</div>
        <div className="st-stat-value">{value}</div>
        <div className="st-stat-sub">{sub}</div>
      </div>
    </div>
  );
}

export function OverviewPage({ nav }: { nav: Nav }) {
  const { items, posts, source, worker, postsError, instagram, tiktok } = useStudio();
  const [filter, setFilter] = useState<Filter>("todos");
  const live = items.filter((i) => i.display !== "arquivado");
  const now = Date.now();
  const today = new Date().toDateString();
  const todayPosts = posts.filter(
    (p) => (p.status === "agendado" || p.status === "publicando") && p.scheduledAt && new Date(p.scheduledAt).toDateString() === today
  );
  const week = now + 7 * 24 * 3600 * 1000;
  const upcoming = posts
    .filter((p) => p.status === "agendado" && p.scheduledAt && new Date(p.scheduledAt).getTime() >= now)
    .sort((a, b) => (a.scheduledAt! < b.scheduledAt! ? -1 : 1));
  const month = new Date();
  const publishedThisMonth = posts.filter((p) => {
    const at = p.publishedAt ?? p.scheduledAt;
    if (p.status !== "publicado" || !at) return false;
    const d = new Date(at);
    return d.getMonth() === month.getMonth() && d.getFullYear() === month.getFullYear();
  }).length;
  const awaiting = source === "banco" ? posts.filter((p) => p.status === "aguardando_aprovacao").length : live.filter((i) => i.display === "rascunho").length;

  // Alertas operacionais (so no modo banco, onde ha fila e confirmacao).
  const alerts: { text: string; post?: Post; action?: () => void }[] = [];
  if (postsError) alerts.push({ text: postsError, action: () => nav.go("publicacoes") });
  if (source === "banco") {
    for (const p of posts) {
      if (p.status === "publicando" && p.scheduledAt && now - new Date(p.scheduledAt).getTime() > 3600_000) {
        alerts.push({ text: `${POST_STATUS.publicando} desde ${new Date(p.scheduledAt).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}: confirme ou marque a falha.`, post: p });
      }
      if (p.status === "falhou") alerts.push({ text: `Publicação falhou: ${p.failureReason ?? "sem motivo"}`, post: p });
      const failed = p.jobs?.find((j) => j.status === "falhou");
      if (failed && p.status !== "publicado" && p.status !== "cancelado") alerts.push({ text: `Lembrete do Telegram não saiu: ${failed.lastError ?? ""}`, post: p });
    }
    const due = posts.some((p) => p.status === "agendado" && p.scheduledAt && new Date(p.scheduledAt).getTime() < now - 5 * 60_000);
    const stale = !worker || now - new Date(worker.last_run_at).getTime() > 10 * 60_000;
    if (due && stale) alerts.push({ text: "Há publicação agendada que já passou da hora e a automação não rodou: confira o agendador do Supabase.", action: () => nav.go("publicacoes") });
  }

  return (
    <FolderGate>
      <div className="st-stats">
        <Stat
          icon="grid"
          tone="violet"
          label="Conteúdos"
          value={live.length}
          sub={`${plural(live.filter((i) => i.kind === "image").length, "imagem", "imagens")} · ${plural(live.filter((i) => i.kind === "video").length, "vídeo", "vídeos")}`}
        />
        <Stat
          icon="check"
          tone="green"
          label="Aguardando aprovação"
          value={awaiting}
          sub={
            source === "banco"
              ? `${plural(posts.filter((p) => p.status === "aprovado" || p.status === "agendado").length, "publicação aprovada", "publicações aprovadas")}`
              : `${plural(live.filter((i) => i.display === "aprovado").length, "aprovado", "aprovados")}, prontos para usar`
          }
        />
        <Stat
          icon="calendar"
          tone="blue"
          label="Publicações hoje"
          value={todayPosts.length}
          sub={`${upcoming.filter((p) => new Date(p.scheduledAt!).getTime() <= week).length} nos próximos 7 dias`}
        />
        <Stat icon="send" tone="pink" label="Publicados" value={publishedThisMonth} sub="neste mês (confirmados)" />
      </div>

      {alerts.length > 0 && (
        <section className="st-panel st-alerts">
          <h2>Alertas</h2>
          <ul>
            {alerts.map((a, i) => (
              <li key={i}>
                <button type="button" className="st-link" onClick={() => (a.post ? nav.openComposer({ post: a.post }) : a.action?.())}>
                  {a.post ? `${NETWORKS[a.post.network].label} · ` : ""}
                  {a.text}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="st-dash">
        <section className="st-panel st-dash-recent">
          <div className="st-panel-head">
            <h2>Conteúdos Recentes</h2>
            <button type="button" className="st-link" onClick={() => nav.go("conteudos")}>
              Ver todos →
            </button>
          </div>
          <FilterTabs value={filter} options={["todos", "imagens", "videos", "aprovados", "rascunhos"]} onChange={setFilter} />
          {applyFilter(items, filter).length === 0 ? (
            <div className="st-empty">
              Nenhum arquivo aqui ainda.{" "}
              <button type="button" className="st-link" onClick={nav.pickFiles}>
                Adicionar conteúdo
              </button>
            </div>
          ) : (
            <div className="st-grid st-grid-4">
              {applyFilter(items, filter)
                .slice(0, 8)
                .map((item) => (
                  <ContentCard key={item.path} item={item} onOpen={() => nav.openItem(item.path)} />
                ))}
            </div>
          )}
        </section>

        <section className="st-panel st-dash-calendar">
          <div className="st-panel-head">
            <h2>Calendário de Publicações</h2>
          </div>
          <Calendar onOpenPost={(post) => nav.openComposer({ post })} onNew={(date) => nav.openComposer({ date })} />
        </section>

        <section className="st-panel st-dash-composer">
          <div className="st-panel-head">
            <h2>Preparar Publicação</h2>
          </div>
          <Composer />
        </section>

        <section className="st-panel st-dash-next">
          <div className="st-panel-head">
            <h2>Próximas publicações</h2>
            <button type="button" className="st-link" onClick={() => nav.go("publicacoes")}>
              Ver todas →
            </button>
          </div>
          <PostList posts={upcoming.slice(0, 5)} nav={nav} empty="Nada agendado. Use o calendário ou o Preparar Publicação." />
          <h3 className="st-subhead">Conexões</h3>
          <div className="st-integrations">
            <button type="button" className="st-integration" onClick={() => nav.go("configuracoes")}>
              <Icon name="folder" size={18} />
              <span>Banco de dados</span>
              <em className={source === "banco" ? "on" : ""}>{source === "banco" ? "conectado" : "não configurado (dados na pasta)"}</em>
            </button>
            {source === "banco" && (
              <button type="button" className="st-integration" onClick={() => nav.go("publicacoes")}>
                <Icon name="refresh" size={18} />
                <span>Lembretes automáticos</span>
                <em className={worker && Date.now() - new Date(worker.last_run_at).getTime() < 10 * 60_000 ? "on" : ""}>
                  {worker
                    ? `última execução ${new Date(worker.last_run_at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}`
                    : "agendador ainda não configurado"}
                </em>
              </button>
            )}
            <button type="button" className="st-integration" onClick={() => nav.go("instagram")}>
              <Icon name="instagram" size={18} />
              <span>Instagram</span>
              <em className={instagram?.connected ? "on" : ""}>
                {instagram?.connected ? `@${instagram.username} · publicação automática` : "não conectado · publicação manual"}
              </em>
            </button>
            <button type="button" className="st-integration" onClick={() => nav.go("tiktok")}>
              <Icon name="tiktok" size={18} />
              <span>TikTok</span>
              <em className={tiktok?.connected ? "on" : ""}>
                {tiktok?.connected ? `${tiktok.name ?? "conectado"} · rascunho automático` : "não conectado · publicação manual"}
              </em>
            </button>
            <button type="button" className="st-integration" onClick={() => nav.go("telegram")}>
              <Icon name="telegram" size={18} />
              <span>Bot do Telegram</span>
              <em>não configurado (Fase 3)</em>
            </button>
          </div>
        </section>
      </div>
    </FolderGate>
  );
}

function PostList({ posts, nav, empty }: { posts: Post[]; nav: Nav; empty: string }) {
  const { items } = useStudio();
  const byPath = new Map(items.map((i) => [i.path, i]));
  if (!posts.length) return <div className="st-empty st-empty-small">{empty}</div>;
  return (
    <div className="st-post-list">
      {posts.map((p) => {
        const cover = p.media.map((m) => byPath.get(m)).find(Boolean);
        return (
          <button key={p.id} type="button" className="st-post-row" onClick={() => nav.openComposer({ post: p })}>
            <span className="st-post-cover">
              {cover ? cover.kind === "image" ? <img src={cover.url} alt="" /> : <video src={`${cover.url}#t=0.1`} muted preload="metadata" /> : <Icon name="image" size={18} />}
            </span>
            <span className="st-post-info">
              <span className="st-post-net">
                <Icon name={NETWORKS[p.network].icon} size={14} />
                {NETWORKS[p.network].label} · {p.formats.join(", ") || "sem destino"}
              </span>
              <span className="st-post-caption">{p.caption || "Sem legenda"}</span>
            </span>
            <span className="st-post-when">
              {p.scheduledAt ? new Date(p.scheduledAt).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "sem data"}
              <PostStatusBadge status={p.status} post={p} />
            </span>
          </button>
        );
      })}
    </div>
  );
}

// --- Conteudos ---------------------------------------------------------------------

export function ContentsPage({ nav, query }: { nav: Nav; query: string }) {
  const { items, addFiles, refresh, characters, campaigns } = useStudio();
  const [filter, setFilter] = useState<Filter>("todos");
  const [extra, setExtra] = useState<Extra>({});
  const [dragging, setDragging] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const list = applyFilter(items, filter, query, extra);

  async function drop(files: File[]) {
    const { added, skipped } = await addFiles(files);
    setNote(
      [added ? `${added} arquivo(s) copiado(s) para a pasta.` : "", skipped.length ? `Ignorados (tipo não aceito): ${skipped.join(", ")}` : ""]
        .filter(Boolean)
        .join(" ")
    );
  }

  return (
    <div>
      <div className="st-page-head">
        <div>
          <h1>Conteúdos</h1>
          <p>Imagens e vídeos da pasta do estúdio, no seu computador.</p>
        </div>
        <WhenReady>
          <div className="st-head-actions">
            <button type="button" className="st-btn" onClick={refresh} title="Ler a pasta de novo (arquivos salvos pelo Explorer)">
              <Icon name="refresh" size={16} />
              Atualizar
            </button>
            <button type="button" className="st-btn st-btn-primary" onClick={nav.pickFiles}>
              <Icon name="plus" size={16} />
              Adicionar
            </button>
          </div>
        </WhenReady>
      </div>
      <FolderGate>
        <div
          className={dragging ? "st-dropzone dragging" : "st-dropzone"}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (e.dataTransfer.files.length) drop([...e.dataTransfer.files]);
          }}
        >
          Arraste imagens e vídeos para cá: eles são copiados para a pasta <b>conteudos</b>. Arquivos salvos direto na pasta
          aparecem ao clicar em Atualizar.
        </div>
        {note && <p className="st-ok">{note}</p>}
        <FilterTabs
          value={filter}
          options={["todos", "imagens", "videos", "aprovados", "rascunhos", "agendados", "arquivados"]}
          onChange={setFilter}
        />
        <div className="st-filters">
          <select className="st-input" value={extra.characterId ?? ""} onChange={(e) => setExtra({ ...extra, characterId: e.target.value || undefined })} aria-label="Personagem">
            <option value="">Todas as personagens</option>
            {characters.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <select className="st-input" value={extra.campaignId ?? ""} onChange={(e) => setExtra({ ...extra, campaignId: e.target.value || undefined })} aria-label="Campanha">
            <option value="">Todas as campanhas</option>
            {campaigns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <select className="st-input" value={extra.audience ?? ""} onChange={(e) => setExtra({ ...extra, audience: e.target.value || undefined })} aria-label="Público">
            <option value="">Promocional e assinantes</option>
            <option value="promocional">Promocional</option>
            <option value="assinantes">Exclusivo para assinantes</option>
          </select>
        </div>
        {query && <p className="st-hint">Buscando “{query}”: {list.length} resultado(s).</p>}
        {list.length === 0 ? (
          <div className="st-empty">{filter === "arquivados" ? "Nada arquivado." : "Nenhum arquivo aqui."}</div>
        ) : (
          <div className="st-grid">
            {list.map((item) => (
              <ContentCard key={item.path} item={item} onOpen={() => nav.openItem(item.path)} />
            ))}
          </div>
        )}
      </FolderGate>
    </div>
  );
}

export function ItemDetails({ path, nav, onClose }: { path: string; nav: Nav; onClose: () => void }) {
  const { items, posts, updateItem, characters, campaigns, source } = useStudio();
  const item = items.find((i) => i.path === path);
  const [form, setForm] = useState<MetaPatch>(() => ({
    title: item?.title ?? "",
    description: item?.description ?? "",
    characterId: item?.characterId ?? null,
    category: item?.category ?? "",
    campaignId: item?.campaignId ?? null,
    caption: item?.caption ?? "",
    destinations: item?.destinations ?? [],
    rating: item?.rating ?? "livre",
    audience: item?.audience ?? "promocional",
    status: item?.status ?? "rascunho",
  }));
  const [tags, setTags] = useState((item?.hashtags ?? []).join(" "));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  if (!item) return <div className="st-empty">Arquivo não encontrado na pasta.</div>;
  const usedIn = posts.filter((p) => p.media.includes(path));
  const categories = [...new Set(items.map((i) => i.category).filter((c): c is string => Boolean(c)))].sort();
  const set = (patch: MetaPatch) => {
    setForm((f) => ({ ...f, ...patch }));
    setMessage(null);
  };
  const toggleDestination = (n: Network) =>
    set({ destinations: form.destinations?.includes(n) ? form.destinations.filter((d) => d !== n) : [...(form.destinations ?? []), n] });

  async function save() {
    const hashtags = tags
      .split(/[\s,]+/)
      .map((t) => t.replace(/^#+/, "").trim())
      .filter(Boolean)
      .map((t) => `#${t}`);
    const patch: MetaPatch = { ...form, title: form.title?.trim(), hashtags: [...new Set(hashtags)] };
    const problem = metaProblem({ ...patch, title: patch.title ?? "" });
    if (problem) return setMessage({ ok: false, text: problem });
    setSaving(true);
    try {
      await updateItem(path, patch);
      setMessage({ ok: true, text: source === "banco" ? "Salvo no banco." : "Salvo na pasta." });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="st-details">
      <div className="st-details-media">
        {item.kind === "image" ? <img src={item.url} alt={item.title} /> : <video src={item.url} controls playsInline />}
      </div>
      <div className="st-details-side">
        <label className="st-label" htmlFor="st-title">Nome</label>
        <input id="st-title" className="st-input" value={form.title ?? ""} onChange={(e) => set({ title: e.target.value })} />

        <label className="st-label" htmlFor="st-desc">Descrição</label>
        <textarea id="st-desc" className="st-input st-area" rows={2} value={form.description ?? ""} onChange={(e) => set({ description: e.target.value })} />

        <div className="st-form-grid">
          <div>
            <label className="st-label" htmlFor="st-char">Personagem</label>
            <select id="st-char" className="st-input" value={form.characterId ?? ""} onChange={(e) => set({ characterId: e.target.value || null })}>
              <option value="">Nenhuma</option>
              {characters.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="st-label" htmlFor="st-camp">Campanha</label>
            <select id="st-camp" className="st-input" value={form.campaignId ?? ""} onChange={(e) => set({ campaignId: e.target.value || null })}>
              <option value="">Nenhuma</option>
              {campaigns.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="st-label" htmlFor="st-cat">Categoria</label>
            <input
              id="st-cat"
              className="st-input"
              list="st-categories"
              value={form.category ?? ""}
              onChange={(e) => set({ category: e.target.value })}
              placeholder="ex.: ensaio, bastidores"
            />
            <datalist id="st-categories">
              {categories.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </div>
          <div>
            <label className="st-label" htmlFor="st-aud">Público</label>
            <select id="st-aud" className="st-input" value={form.audience} onChange={(e) => set({ audience: e.target.value as Audience })}>
              <option value="promocional">Promocional (divulgação)</option>
              <option value="assinantes">Exclusivo para assinantes</option>
            </select>
          </div>
        </div>

        <span className="st-label">Classificação</span>
        <div className="st-seg">
          {(["livre", "sensivel", "adulto"] as const).map((r) => (
            <button
              key={r}
              type="button"
              className={form.rating === r ? "active" : ""}
              onClick={() =>
                set(r === "adulto" ? { rating: r, destinations: (form.destinations ?? []).filter((d) => d === "telegram") } : { rating: r })
              }
            >
              {r === "livre" ? "Livre" : r === "sensivel" ? "Sensível" : "Adulto (+18)"}
            </button>
          ))}
        </div>

        <span className="st-label">Destinos permitidos</span>
        <div className="st-row st-row-tight">
          {(Object.keys(NETWORKS) as Network[]).map((n) => {
            const blocked = form.rating === "adulto" && n !== "telegram";
            return (
              <label key={n} className="st-check" title={blocked ? "Instagram e TikTok proíbem conteúdo adulto" : undefined}>
                <input type="checkbox" checked={form.destinations?.includes(n) ?? false} disabled={blocked} onChange={() => toggleDestination(n)} />
                {NETWORKS[n].label}
              </label>
            );
          })}
        </div>

        <label className="st-label" htmlFor="st-cap">Legenda sugerida</label>
        <textarea id="st-cap" className="st-input st-area" rows={3} value={form.caption ?? ""} onChange={(e) => set({ caption: e.target.value })} />
        <label className="st-label" htmlFor="st-tags">Hashtags</label>
        <input
          id="st-tags"
          className="st-input"
          value={tags}
          onChange={(e) => {
            setTags(e.target.value);
            setMessage(null);
          }}
          placeholder="#lunavox #lifestyle"
        />

        <span className="st-label">Status</span>
        <div className="st-seg">
          {(["rascunho", "aprovado", "arquivado"] as const).map((st) => (
            <button key={st} type="button" className={form.status === st ? "active" : ""} onClick={() => set({ status: st as ItemStatus })}>
              {st === "rascunho" ? "Rascunho" : st === "aprovado" ? "Aprovado" : "Arquivado"}
            </button>
          ))}
        </div>

        {message && <p className={message.ok ? "st-ok" : "st-error"}>{message.text}</p>}
        <div className="st-composer-actions">
          <button
            type="button"
            className="st-btn"
            onClick={() => {
              onClose();
              nav.openComposer({ media: [path] });
            }}
            disabled={item.status === "arquivado"}
          >
            <Icon name="send" size={16} />
            Usar em publicação
          </button>
          <button type="button" className="st-btn st-btn-primary" onClick={save} disabled={saving}>
            {saving ? "Salvando..." : "Salvar alterações"}
          </button>
        </div>

        <dl className="st-facts">
          <dt>Arquivo</dt>
          <dd>conteudos/{item.path}</dd>
          <dt>Tamanho</dt>
          <dd>{formatBytes(item.size)}</dd>
          <dt>Adicionado</dt>
          <dd>{formatDate(item.addedAt)}</dd>
          <dt>Uso</dt>
          <dd>
            {usedIn.length
              ? usedIn.map((p) => `${NETWORKS[p.network].label} ${p.scheduledAt ? formatDate(p.scheduledAt) : ""} (${p.status})`).join(" · ")
              : "ainda não usado"}
          </dd>
        </dl>
        <p className="st-hint">Arquivar só esconde: o arquivo continua na pasta. Para apagar de vez, apague pelo Explorer.</p>
      </div>
    </div>
  );
}

// --- Calendario e Publicacoes -----------------------------------------------------

export function CalendarPage({ nav }: { nav: Nav }) {
  return (
    <div>
      <div className="st-page-head">
        <h1>Calendário</h1>
      </div>
      <FolderGate>
        <section className="st-panel">
          <Calendar onOpenPost={(post) => nav.openComposer({ post })} onNew={(date) => nav.openComposer({ date })} />
        </section>
      </FolderGate>
    </div>
  );
}

// Abas do modo banco (fluxo com aprovacao) e do modo pasta (simples).
const CLOUD_TABS: { id: string; label: string; statuses: PostStatus[] }[] = [
  { id: "aprovar", label: "Aguardando aprovação", statuses: ["aguardando_aprovacao"] },
  { id: "agenda", label: "Aprovadas e agendadas", statuses: ["aprovado", "agendado"] },
  { id: "confirmar", label: "Processando / ação manual", statuses: ["publicando"] },
  { id: "publicadas", label: "Publicadas", statuses: ["publicado"] },
  { id: "rascunhos", label: "Rascunhos", statuses: ["rascunho"] },
  { id: "problemas", label: "Falhas e canceladas", statuses: ["falhou", "cancelado"] },
];
const FOLDER_TABS: typeof CLOUD_TABS = [
  { id: "agenda", label: "Agendadas", statuses: ["agendado"] },
  { id: "rascunhos", label: "Rascunhos", statuses: ["rascunho"] },
  { id: "publicadas", label: "Publicadas", statuses: ["publicado"] },
];

function WorkerPanel() {
  const { worker, runJobs } = useStudio();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const minutes = worker ? Math.round((Date.now() - new Date(worker.last_run_at).getTime()) / 60000) : null;
  return (
    <div className="st-worker">
      <span>
        <b>Lembretes automáticos:</b>{" "}
        {worker
          ? `última execução ${minutes! < 1 ? "agora há pouco" : `há ${minutes} min`} (${worker.last_result.origem === "cron" ? "agendador do Supabase" : "botão do painel"})${worker.last_result.erro ? ` · erro: ${worker.last_result.erro}` : ""}`
          : "nunca executou. Configure o agendador do Supabase (docs/PLANO_PLATAFORMA.md) ou use o botão."}
        {note && <em> · {note}</em>}
      </span>
      <button
        type="button"
        className="st-btn st-btn-small"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            const r = await runJobs();
            setNote(`${r.processadas} tarefa(s): ${r.ok} ok, ${r.novas_tentativas} para tentar de novo, ${r.falhas} com falha`);
          } catch (e) {
            setNote(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Icon name="refresh" size={14} />
        Processar agora
      </button>
    </div>
  );
}

export function PublicationsPage({ nav }: { nav: Nav }) {
  const { posts, source, postsError } = useStudio();
  const tabs = source === "banco" ? CLOUD_TABS : FOLDER_TABS;
  const [tabId, setTabId] = useState(tabs[0].id);
  const tab = tabs.find((t) => t.id === tabId) ?? tabs[0];
  const newestFirst = tab.id === "publicadas" || tab.id === "problemas";
  const list = posts
    .filter((p) => tab.statuses.includes(p.status))
    .sort((a, b) => ((a.scheduledAt ?? a.createdAt) < (b.scheduledAt ?? b.createdAt) ? (newestFirst ? 1 : -1) : newestFirst ? -1 : 1));
  return (
    <div>
      <div className="st-page-head">
        <h1>Publicações</h1>
        <WhenReady>
          <button type="button" className="st-btn st-btn-primary" onClick={() => nav.openComposer({})}>
            <Icon name="plus" size={16} />
            Nova publicação
          </button>
        </WhenReady>
      </div>
      <FolderGate>
        <section className="st-panel">
          {postsError && <p className="st-warn">{postsError}</p>}
          {source === "banco" && !postsError && <WorkerPanel />}
          <div className="st-tabs">
            {tabs.map((t) => (
              <button key={t.id} type="button" className={tab.id === t.id ? "active" : ""} onClick={() => setTabId(t.id)}>
                {t.label} ({posts.filter((p) => t.statuses.includes(p.status)).length})
              </button>
            ))}
          </div>
          <PostList posts={list} nav={nav} empty="Nenhuma publicação aqui." />
        </section>
      </FolderGate>
    </div>
  );
}

// --- Personagens ----------------------------------------------------------------------

export function CharactersPage() {
  const { characters, items, saveCharacter, state } = useStudio();
  const [editing, setEditing] = useState<(Omit<Character, "id"> & { id?: string }) | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!editing) return;
    try {
      await saveCharacter(editing);
      setEditing(null);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div>
      <div className="st-page-head">
        <div>
          <h1>Personagens</h1>
          <p>Cada conteúdo pode ser ligado a uma personagem.</p>
        </div>
        <WhenReady>
          <button type="button" className="st-btn st-btn-primary" onClick={() => setEditing({ name: "", description: "", active: true })}>
            <Icon name="plus" size={16} />
            Nova personagem
          </button>
        </WhenReady>
      </div>
      <FolderGate>
        <div className="st-list">
          {characters.map((c) => (
            <div key={c.id} className="st-panel st-list-row">
              <div>
                <b>{c.name}</b> {!c.active && <span className="st-badge st-badge-arquivado">inativa</span>}
                <p>{c.description || "Sem descrição"}</p>
              </div>
              <span className="st-list-count">{plural(items.filter((i) => i.characterId === c.id).length, "conteúdo", "conteúdos")}</span>
              <button type="button" className="st-btn st-btn-small" onClick={() => setEditing({ ...c, description: c.description ?? "" })}>
                Editar
              </button>
            </div>
          ))}
        </div>
      </FolderGate>
      {editing && state === "ready" && (
        <Modal title={editing.id ? "Editar personagem" : "Nova personagem"} onClose={() => setEditing(null)}>
          <label className="st-label" htmlFor="ch-name">Nome</label>
          <input id="ch-name" className="st-input st-full" value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
          <label className="st-label" htmlFor="ch-desc">Descrição</label>
          <textarea
            id="ch-desc"
            className="st-input st-area"
            rows={3}
            value={editing.description ?? ""}
            onChange={(e) => setEditing({ ...editing, description: e.target.value })}
          />
          <label className="st-check st-mt">
            <input type="checkbox" checked={editing.active} onChange={(e) => setEditing({ ...editing, active: e.target.checked })} />
            Ativa
          </label>
          {error && <p className="st-error">{error}</p>}
          <div className="st-composer-actions">
            <button type="button" className="st-btn" onClick={() => setEditing(null)}>
              Cancelar
            </button>
            <button type="button" className="st-btn st-btn-primary" onClick={save}>
              Salvar
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

// --- Configuracoes -------------------------------------------------------------------

const CAMPAIGN_STATUS: Record<Campaign["status"], string> = { ativa: "Ativa", pausada: "Pausada", encerrada: "Encerrada" };

function CampaignsPanel() {
  const { campaigns, items, saveCampaign } = useStudio();
  const [editing, setEditing] = useState<(Omit<Campaign, "id"> & { id?: string }) | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!editing) return;
    try {
      await saveCampaign({ ...editing, startsOn: editing.startsOn || null, endsOn: editing.endsOn || null });
      setEditing(null);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <section className="st-panel st-settings">
      <div className="st-panel-head">
        <h2>Campanhas</h2>
        <button
          type="button"
          className="st-btn st-btn-small"
          onClick={() => setEditing({ name: "", description: "", startsOn: null, endsOn: null, status: "ativa" })}
        >
          <Icon name="plus" size={14} />
          Nova campanha
        </button>
      </div>
      {campaigns.length === 0 ? (
        <div className="st-empty st-empty-small">Nenhuma campanha ainda.</div>
      ) : (
        <div className="st-list">
          {campaigns.map((c) => (
            <div key={c.id} className="st-list-row st-list-row-flat">
              <div>
                <b>{c.name}</b> <span className="st-badge st-badge-rascunho">{CAMPAIGN_STATUS[c.status]}</span>
                <p>
                  {c.startsOn ? formatDate(`${c.startsOn}T12:00:00`) : "sem início"} → {c.endsOn ? formatDate(`${c.endsOn}T12:00:00`) : "sem fim"}
                  {c.description ? ` · ${c.description}` : ""}
                </p>
              </div>
              <span className="st-list-count">{plural(items.filter((i) => i.campaignId === c.id).length, "conteúdo", "conteúdos")}</span>
              <button type="button" className="st-btn st-btn-small" onClick={() => setEditing({ ...c, description: c.description ?? "" })}>
                Editar
              </button>
            </div>
          ))}
        </div>
      )}
      {editing && (
        <Modal title={editing.id ? "Editar campanha" : "Nova campanha"} onClose={() => setEditing(null)}>
          <label className="st-label" htmlFor="cp-name">Nome</label>
          <input id="cp-name" className="st-input st-full" value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
          <label className="st-label" htmlFor="cp-desc">Descrição</label>
          <textarea
            id="cp-desc"
            className="st-input st-area"
            rows={2}
            value={editing.description ?? ""}
            onChange={(e) => setEditing({ ...editing, description: e.target.value })}
          />
          <div className="st-form-grid">
            <div>
              <label className="st-label" htmlFor="cp-start">Início</label>
              <input id="cp-start" type="date" className="st-input" value={editing.startsOn ?? ""} onChange={(e) => setEditing({ ...editing, startsOn: e.target.value })} />
            </div>
            <div>
              <label className="st-label" htmlFor="cp-end">Fim</label>
              <input id="cp-end" type="date" className="st-input" value={editing.endsOn ?? ""} onChange={(e) => setEditing({ ...editing, endsOn: e.target.value })} />
            </div>
          </div>
          <span className="st-label">Status</span>
          <div className="st-seg">
            {(["ativa", "pausada", "encerrada"] as const).map((st) => (
              <button key={st} type="button" className={editing.status === st ? "active" : ""} onClick={() => setEditing({ ...editing, status: st })}>
                {CAMPAIGN_STATUS[st]}
              </button>
            ))}
          </div>
          {error && <p className="st-error">{error}</p>}
          <div className="st-composer-actions">
            <button type="button" className="st-btn" onClick={() => setEditing(null)}>
              Cancelar
            </button>
            <button type="button" className="st-btn st-btn-primary" onClick={save}>
              Salvar
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}

function HistoryPanel() {
  const { source, log } = useStudio();
  const [rows, setRows] = useState<{ at: string; text: string }[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (source === "pasta") {
      setRows(
        [...log]
          .reverse()
          .slice(0, 100)
          .map((l) => ({ at: l.at, text: `${l.action} · ${l.detail}` }))
      );
      return;
    }
    cloudAudit()
      .then((logs) =>
        setRows(
          logs.map((l) => ({
            at: l.at,
            text: `${l.action} · ${l.entity}${l.details && Object.keys(l.details).length ? ` · ${JSON.stringify(l.details)}` : ""}`,
          }))
        )
      )
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [source, log]);

  return (
    <section className="st-panel st-settings">
      <h2>Histórico de operações</h2>
      {error && <p className="st-error">{error}</p>}
      {rows && rows.length === 0 && <div className="st-empty st-empty-small">Nenhuma operação registrada.</div>}
      {rows && rows.length > 0 && (
        <ul className="st-history">
          {rows.map((r, i) => (
            <li key={i}>
              <time>{new Date(r.at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}</time>
              <span>{r.text}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function SettingsPage() {
  const { state, source, folderName, items, posts, totalBytes, connect, forget, refresh } = useStudio();
  return (
    <div>
      <div className="st-page-head">
        <h1>Configurações</h1>
      </div>
      <WorkerPcPanel />
      <section className="st-panel st-settings">
        <h2>Pasta do estúdio (arquivos)</h2>
        {state === "ready" ? (
          <>
            <p>
              Conectada: <b>{folderName}</b> · {items.length} arquivo(s), {formatBytes(totalBytes)} · {posts.length} publicação(ões)
            </p>
            <p className="st-hint">
              As imagens e os vídeos ficam só nesta pasta, no seu computador. Faça backup copiando a pasta inteira (inclui
              conteudos/ e luna-vox.json, que também guarda uma cópia dos dados).
            </p>
            <div className="st-head-actions">
              <button type="button" className="st-btn" onClick={refresh}>
                <Icon name="refresh" size={16} />
                Ler a pasta de novo
              </button>
              <button type="button" className="st-btn" onClick={connect}>
                <Icon name="folder" size={16} />
                Trocar de pasta
              </button>
              <button type="button" className="st-btn st-btn-danger" onClick={forget}>
                Desconectar
              </button>
            </div>
          </>
        ) : (
          <FolderGate>{null}</FolderGate>
        )}
      </section>

      <section className="st-panel st-settings">
        <h2>Banco de dados (dados e histórico)</h2>
        {source === "banco" ? (
          <p>
            <span className="st-badge st-badge-aprovado">Conectado</span> Supabase. Nomes, status, personagens, campanhas e o
            histórico ficam no banco; os arquivos continuam na pasta.
          </p>
        ) : (
          <p>
            <span className="st-badge st-badge-rascunho">Não configurado</span> Os dados ficam no luna-vox.json da pasta. Ao
            ligar o Supabase na Vercel (SUPABASE_URL e SUPABASE_SECRET_KEY), o estúdio leva esses dados para o banco
            sozinho na próxima vez que abrir a pasta.
          </p>
        )}
      </section>

      {state === "ready" && (
        <>
          <CampaignsPanel />
          <HistoryPanel />
        </>
      )}
    </div>
  );
}
