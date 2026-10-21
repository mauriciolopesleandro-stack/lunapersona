import { useState } from "react";
import { Calendar } from "./Calendar";
import { Composer } from "./Composer";
import { Icon } from "./Icon";
import { FolderGate, NETWORKS, WhenReady, StatusBadge, Thumb, formatBytes, formatDate } from "./shared";
import { useStudio, type Item, type ItemStatus, type Post } from "./store";

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

function applyFilter(items: Item[], filter: Filter, query = "") {
  const q = query.trim().toLowerCase();
  return items.filter((i) => {
    if (filter === "arquivados" ? i.display !== "arquivado" : i.display === "arquivado") return false;
    if (filter === "imagens" && i.kind !== "image") return false;
    if (filter === "videos" && i.kind !== "video") return false;
    if (filter === "aprovados" && i.display !== "aprovado") return false;
    if (filter === "rascunhos" && i.display !== "rascunho") return false;
    if (filter === "agendados" && i.display !== "agendado") return false;
    return !q || i.title.toLowerCase().includes(q) || i.path.toLowerCase().includes(q);
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
        <StatusBadge status={item.display} />
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
  const { items, posts } = useStudio();
  const [filter, setFilter] = useState<Filter>("todos");
  const live = items.filter((i) => i.display !== "arquivado");
  const now = Date.now();
  const week = now + 7 * 24 * 3600 * 1000;
  const upcoming = posts
    .filter((p) => p.status === "agendado" && p.scheduledAt && new Date(p.scheduledAt).getTime() >= now)
    .sort((a, b) => (a.scheduledAt! < b.scheduledAt! ? -1 : 1));
  const month = new Date();
  const publishedThisMonth = posts.filter((p) => {
    if (p.status !== "publicado" || !p.scheduledAt) return false;
    const d = new Date(p.scheduledAt);
    return d.getMonth() === month.getMonth() && d.getFullYear() === month.getFullYear();
  }).length;

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
        <Stat icon="check" tone="green" label="Aprovados" value={live.filter((i) => i.display === "aprovado").length} sub="prontos para publicar" />
        <Stat
          icon="calendar"
          tone="blue"
          label="Agendados"
          value={upcoming.length}
          sub={`${upcoming.filter((p) => new Date(p.scheduledAt!).getTime() <= week).length} nos próximos 7 dias`}
        />
        <Stat icon="send" tone="pink" label="Publicados" value={publishedThisMonth} sub="neste mês" />
      </div>

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
          <div className="st-integrations">
            {(["instagram", "tiktok", "telegram"] as const).map((n) => (
              <button key={n} type="button" className="st-integration" onClick={() => nav.go(n)}>
                <Icon name={NETWORKS[n].icon} size={18} />
                <span>{NETWORKS[n].label}</span>
                <em>integração em breve</em>
              </button>
            ))}
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
              <span className={`st-badge st-badge-${p.status}`}>{p.status === "agendado" ? "Agendado" : p.status === "publicado" ? "Publicado" : "Rascunho"}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

// --- Conteudos ---------------------------------------------------------------------

export function ContentsPage({ nav, query }: { nav: Nav; query: string }) {
  const { items, addFiles, refresh } = useStudio();
  const [filter, setFilter] = useState<Filter>("todos");
  const [dragging, setDragging] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const list = applyFilter(items, filter, query);

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
  const { items, posts, updateItem } = useStudio();
  const item = items.find((i) => i.path === path);
  const [title, setTitle] = useState(item?.title ?? "");
  if (!item) return <div className="st-empty">Arquivo não encontrado na pasta.</div>;
  const usedIn = posts.filter((p) => p.media.includes(path));
  const setStatus = (status: ItemStatus) => updateItem(path, { status });

  return (
    <div className="st-details">
      <div className="st-details-media">
        {item.kind === "image" ? <img src={item.url} alt={item.title} /> : <video src={item.url} controls playsInline />}
      </div>
      <div className="st-details-side">
        <label className="st-label" htmlFor="st-title">Título</label>
        <input
          id="st-title"
          className="st-input"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => title.trim() && title !== item.title && updateItem(path, { title: title.trim() })}
        />
        <span className="st-label">Status</span>
        <div className="st-seg">
          {(["rascunho", "aprovado", "arquivado"] as const).map((s) => (
            <button key={s} type="button" className={item.status === s ? "active" : ""} onClick={() => setStatus(s)}>
              {s === "rascunho" ? "Rascunho" : s === "aprovado" ? "Aprovado" : "Arquivado"}
            </button>
          ))}
        </div>
        <dl className="st-facts">
          <dt>Arquivo</dt>
          <dd>conteudos/{item.path}</dd>
          <dt>Tamanho</dt>
          <dd>{formatBytes(item.size)}</dd>
          <dt>Adicionado</dt>
          <dd>{formatDate(item.addedAt)}</dd>
          <dt>Publicações</dt>
          <dd>{usedIn.length ? usedIn.map((p) => `${NETWORKS[p.network].label} (${p.status})`).join(", ") : "nenhuma"}</dd>
        </dl>
        <button
          type="button"
          className="st-btn st-btn-primary"
          onClick={() => {
            onClose();
            nav.openComposer({ media: [path] });
          }}
          disabled={item.status === "arquivado"}
        >
          <Icon name="send" size={16} />
          Usar em publicação
        </button>
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

export function PublicationsPage({ nav }: { nav: Nav }) {
  const { posts } = useStudio();
  const [tab, setTab] = useState<"agendado" | "rascunho" | "publicado">("agendado");
  const list = posts
    .filter((p) => p.status === tab)
    .sort((a, b) => ((a.scheduledAt ?? "") < (b.scheduledAt ?? "") ? (tab === "publicado" ? 1 : -1) : tab === "publicado" ? -1 : 1));
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
          <div className="st-tabs">
            {(["agendado", "rascunho", "publicado"] as const).map((s) => (
              <button key={s} type="button" className={tab === s ? "active" : ""} onClick={() => setTab(s)}>
                {s === "agendado" ? "Agendadas" : s === "rascunho" ? "Rascunhos" : "Publicadas"} ({posts.filter((p) => p.status === s).length})
              </button>
            ))}
          </div>
          <PostList posts={list} nav={nav} empty="Nenhuma publicação aqui." />
        </section>
      </FolderGate>
    </div>
  );
}

// --- Configuracoes -------------------------------------------------------------------

export function SettingsPage() {
  const { state, folderName, items, posts, totalBytes, connect, forget, refresh } = useStudio();
  return (
    <div>
      <div className="st-page-head">
        <h1>Configurações</h1>
      </div>
      <section className="st-panel st-settings">
        <h2>Pasta do estúdio</h2>
        {state === "ready" ? (
          <>
            <p>
              Conectada: <b>{folderName}</b> · {items.length} arquivo(s), {formatBytes(totalBytes)} · {posts.length} publicação(ões)
            </p>
            <p className="st-hint">
              Faça backup copiando a pasta inteira (inclui conteudos/ e luna-vox.json). O estúdio também guarda
              luna-vox.backup.json com a versão anterior dos dados a cada visita.
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
    </div>
  );
}
