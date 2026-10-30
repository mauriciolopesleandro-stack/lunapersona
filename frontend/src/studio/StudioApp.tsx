import { useEffect, useRef, useState } from "react";
import { ProfilePage } from "../components/ProfilePage";
import { Composer } from "./Composer";
import { PostarPage, type PostarStart } from "./PostarPage";
import { Icon } from "./Icon";
import { CalendarPage, CharactersPage, ContentsPage, ItemDetails, OverviewPage, PublicationsPage, SettingsPage, type Nav } from "./pages";
import { needsYou, WaitingForYou } from "./PostFlow";
import { ComingSoon, Modal, formatBytes } from "./shared";
import { AccountsPage, InstagramPage, TikTokPage } from "./social";
import { MusicPage } from "./music";
import { Dock } from "./dock";
import { Chat18Page } from "./assistant";
import { FinancePage, PlansPage, SubscriptionsPage, useSalesStatus } from "./money";
import { SalesPage } from "./sales";
import { TelegramFoldersPage } from "./telegramFolders";
import { StudioProvider, useStudio, type Post } from "./store";
import "./studio.css";

// Menu enxuto: o que e do dia a dia de criar fica em "Creator", o que e de
// ganhar dinheiro em "Monetizacao" e as contas em "Configuracoes". O Assistente
// (com agenda e planejamento) e as personagens ficam no dock flutuante.
interface Group {
  label: string;
  icon: string;
  text: string;
  tabs: { id: string; label: string }[];
}
const GROUPS: Record<string, Group> = {
  creator: {
    label: "Creator",
    icon: "star",
    text: "Biblioteca, publicações, músicas e audiência num lugar só.",
    tabs: [
      { id: "conteudos", label: "Biblioteca" },
      { id: "publicacoes", label: "Publicações" },
      { id: "musicas", label: "Músicas e áudios" },
      { id: "audiencia", label: "Audiência" },
    ],
  },
  money: {
    label: "Monetização",
    icon: "dollar",
    text: "Bot de vendas, conteúdo do Telegram, assinantes do VIP e o dinheiro.",
    tabs: [
      { id: "vendas", label: "Vendas" },
      { id: "chat18", label: "Chat +18" },
      { id: "planos", label: "Planos e preços" },
      { id: "telegram", label: "Conteúdo do Telegram" },
      { id: "assinaturas", label: "Assinaturas" },
      { id: "financeiro", label: "Financeiro" },
    ],
  },
  config: {
    label: "Configurações",
    icon: "gear",
    text: "Pasta, banco, robô do PC, campanhas e as contas conectadas.",
    tabs: [
      { id: "configuracoes", label: "Geral" },
      { id: "contas", label: "Contas conectadas" },
    ],
  },
};
// Instagram e TikTok abrem por Contas conectadas
const ALIAS: Record<string, string> = { instagram: "contas", tiktok: "contas" };
const groupOf = (page: string) => Object.keys(GROUPS).find((g) => GROUPS[g].tabs.some((t) => t.id === (ALIAS[page] ?? page))) ?? null;

const NAV: { id: string; label: string; icon: string; group?: string }[] = [
  { id: "visao", label: "Visão Geral", icon: "home" },
  { id: "postar", label: "Publicar", icon: "plus" },
  { id: "conteudos", label: "Creator", icon: "star", group: "creator" },
  { id: "vendas", label: "Monetização", icon: "dollar", group: "money" },
  { id: "configuracoes", label: "Configurações", icon: "gear", group: "config" },
];

// Modulos que dependem de integracoes oficiais ou de dados que ainda nao existem.
const SOON: Record<string, { title: string; icon: string; text: string }> = {
  audiencia: { title: "Audiência", icon: "chart", text: "Seguidores e alcance, vindos das integrações oficiais." },
};

interface Props {
  theme: "dark" | "light";
  onToggleTheme: () => void;
  onLogout: () => void;
}

export function StudioApp(props: Props) {
  return (
    <StudioProvider>
      <Shell {...props} />
    </StudioProvider>
  );
}

function Shell({ theme, onToggleTheme, onLogout }: Props) {
  const studio = useStudio();
  // volta do TikTok / do Facebook (OAuth) cai em /?tiktok=...#tiktok ou /?instagram=...#instagram
  const [page, setPage] = useState(() => (window.location.hash === "#tiktok" ? "tiktok" : window.location.hash === "#instagram" ? "instagram" : "visao"));
  const [menuOpen, setMenuOpen] = useState(false);
  const [userMenu, setUserMenu] = useState(false);
  const [query, setQuery] = useState("");
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [composer, setComposer] = useState<{ post?: Post; date?: Date; media?: string[] } | null>(null);
  const [postar, setPostar] = useState<PostarStart>({ key: 0 });
  const [askAssistant, setAskAssistant] = useState<{ text: string; key: number } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const go = (id: string) => {
    // o Assistente nao e mais uma tela: abre o dock
    if (id === "assistente") {
      setAskAssistant({ text: "", key: Date.now() });
      return;
    }
    setPage(id);
    setMenuOpen(false);
    window.scrollTo(0, 0);
  };

  const nav: Nav = {
    openItem: setOpenPath,
    // publicacao nova no modo banco abre a tela Postar; editar continua no modal
    openComposer: (opts) => {
      if (!opts.post && studio.source === "banco") {
        setPostar({ key: Date.now(), date: opts.date, media: opts.media });
        go("postar");
      } else setComposer(opts);
    },
    go,
    pickFiles: () => {
      if (studio.state === "ready") fileInput.current?.click();
      else go("conteudos");
    },
  };

  const group = groupOf(page);
  const waitingCount = studio.source === "banco" ? studio.posts.filter(needsYou).length : 0;

  return (
    <div className="st-shell">
      {menuOpen && <div className="st-scrim" onClick={() => setMenuOpen(false)} />}
      <aside className={menuOpen ? "st-sidebar open" : "st-sidebar"}>
        <div className="st-brand">
          <span className="st-brand-mark">LV</span>
          <span>
            <span className="st-brand-name">LUNA VOX</span>
            <span className="st-brand-sub">STUDIO</span>
          </span>
        </div>
        <nav className="st-nav">
          {NAV.map((n) => (
            <button key={n.id} type="button" className={(n.group ? group === n.group : page === n.id) ? "active" : ""} onClick={() => go(n.id)}>
              <Icon name={n.icon} />
              {n.label}
              {n.group === "creator" && waitingCount > 0 && (
                <span className="st-nav-count" title="Esperando você publicar">
                  {waitingCount}
                </span>
              )}
            </button>
          ))}
        </nav>
        <div className="st-sidebar-foot">
          <div className="st-persona">
            <img src="/login/avatar-luna.webp" alt="" />
            <span>
              <b>Luna Vox</b>
              <small>Influenciadora Virtual</small>
            </span>
          </div>
          <div className="st-storage">
            <span>Pasta local</span>
            {studio.state === "ready" ? (
              <b>
                {studio.folderName} · {studio.items.length} arquivo(s) · {formatBytes(studio.totalBytes)}
              </b>
            ) : (
              <b>não conectada</b>
            )}
          </div>
        </div>
      </aside>

      <div className="st-main">
        <header className="st-topbar">
          <button type="button" className="st-icon-btn st-menu-btn" onClick={() => setMenuOpen(true)} aria-label="Abrir menu">
            <Icon name="menu" />
          </button>
          <label className="st-search">
            <Icon name="search" size={18} />
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                if (page !== "conteudos") setPage("conteudos");
              }}
              placeholder="Buscar conteúdos..."
              aria-label="Buscar conteúdos"
            />
            <kbd>Ctrl K</kbd>
          </label>
          <button type="button" className="st-btn st-btn-primary st-new" onClick={nav.pickFiles}>
            <Icon name="plus" size={18} />
            <span>Novo Conteúdo</span>
          </button>
          <button type="button" className="st-icon-btn" onClick={onToggleTheme} aria-label={theme === "dark" ? "Tema claro" : "Tema escuro"}>
            <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
          </button>
          <div className="st-user">
            <button type="button" className="st-user-btn" onClick={() => setUserMenu((v) => !v)} aria-expanded={userMenu}>
              <img src="/login/avatar-luna.webp" alt="" />
              <span className="st-user-text">
                <b>Luna Vox</b>
                <small>Admin</small>
              </span>
              <Icon name="chevronDown" size={16} />
            </button>
            {userMenu && (
              <div className="st-user-menu" onMouseLeave={() => setUserMenu(false)}>
                <button
                  type="button"
                  onClick={() => {
                    setUserMenu(false);
                    go("perfil");
                  }}
                >
                  <Icon name="user" size={16} />
                  Meu perfil
                </button>
                <button type="button" onClick={onLogout}>
                  <Icon name="logout" size={16} />
                  Sair
                </button>
              </div>
            )}
          </div>
        </header>

        <main className="st-page">
          {page !== "perfil" && <WaitingForYou onOpen={(post) => setComposer({ post })} />}
          {group && <GroupHead group={group} page={ALIAS[page] ?? page} go={go} waiting={waitingCount} />}
          {page === "visao" && <OverviewPage nav={nav} />}
          {page === "postar" && <PostarPage key={postar.key} start={postar} onDone={() => go("publicacoes")} />}
          {page === "conteudos" && <ContentsPage nav={nav} query={query} />}
          {page === "calendario" && <CalendarPage nav={nav} />}
          {page === "publicacoes" && <PublicationsPage nav={nav} />}
          {page === "configuracoes" && <SettingsPage />}
          {page === "personagens" && <CharactersPage />}
          {page === "instagram" && <InstagramPage />}
          {page === "tiktok" && <TikTokPage />}
          {page === "musicas" && <MusicPage />}
          {page === "vendas" && <SalesPage onFolders={() => go("telegram")} />}
          {page === "telegram" && <TelegramFoldersPage onSales={() => go("vendas")} />}
          {page === "planos" && <PlansPage />}
          {page === "chat18" && <Chat18Page />}
          {page === "assinaturas" && <SubscriptionsPage />}
          {page === "financeiro" && <FinancePage />}
          {page === "contas" && <AccountsPage nav={nav} />}
          {page === "perfil" && <ProfilePage onLogout={onLogout} />}
          {SOON[page] && <ComingSoon title={SOON[page].title} icon={SOON[page].icon} text={SOON[page].text} />}
        </main>
      </div>

      <Dock
        context={group ?? page}
        onOpenPost={(post) => setComposer({ post })}
        onNewPost={(date) => nav.openComposer({ date })}
        go={go}
        request={askAssistant}
      />

      <input
        ref={fileInput}
        type="file"
        multiple
        hidden
        accept="image/jpeg,image/png,image/webp,image/gif,video/mp4,video/quicktime,video/webm,.m4v"
        onChange={async (e) => {
          const files = [...(e.target.files ?? [])];
          e.target.value = "";
          if (!files.length) return;
          const { skipped } = await studio.addFiles(files);
          if (skipped.length) window.alert(`Ignorados (tipo não aceito): ${skipped.join(", ")}`);
          go("conteudos");
        }}
      />

      {openPath && (
        <Modal title="Conteúdo" onClose={() => setOpenPath(null)} wide>
          <ItemDetails path={openPath} nav={nav} onClose={() => setOpenPath(null)} />
        </Modal>
      )}
      {composer && (
        <Modal title={composer.post ? "Editar publicação" : "Nova publicação"} onClose={() => setComposer(null)} wide>
          <Composer initial={composer.post} defaultDate={composer.date} defaultMedia={composer.media} onDone={() => setComposer(null)} />
        </Modal>
      )}
    </div>
  );
}

// Cabecalho dos menus agrupados: titulo, um resumo do que pede atencao e as abas.
function GroupHead({ group, page, go, waiting }: { group: string; page: string; go: (id: string) => void; waiting: number }) {
  const g = GROUPS[group];
  const studio = useStudio();
  const today = new Date().toDateString();
  const chips: { text: string; to: string; hot?: boolean }[] = [];
  if (group === "creator") {
    if (waiting) chips.push({ text: `${waiting} esperando você publicar`, to: "publicacoes", hot: true });
    const todayCount = studio.posts.filter((p) => p.status === "agendado" && p.scheduledAt && new Date(p.scheduledAt).toDateString() === today).length;
    if (todayCount) chips.push({ text: `${todayCount} agendada(s) para hoje`, to: "publicacoes" });
    const drafts = studio.items.filter((i) => i.display === "rascunho").length;
    if (drafts) chips.push({ text: `${drafts} rascunho(s) na biblioteca`, to: "conteudos" });
  }
  const counts: Record<string, number | undefined> = {
    conteudos: group === "creator" ? studio.items.filter((i) => i.display !== "arquivado").length : undefined,
    publicacoes: waiting || undefined,
  };
  return (
    <div className="st-group-head">
      <div className="st-group-title">
        <span className="st-group-icon">
          <Icon name={g.icon} size={20} />
        </span>
        <div>
          <h1>{g.label}</h1>
          <p>{g.text}</p>
        </div>
        {group === "money" ? <MoneyChips go={go} /> : (
          chips.length > 0 && (
            <div className="st-group-chips">
              {chips.map((c) => (
                <button key={c.text} type="button" className={c.hot ? "hot" : ""} onClick={() => go(c.to)}>
                  {c.text}
                </button>
              ))}
            </div>
          )
        )}
      </div>
      <div className="st-group-tabs" role="tablist">
        {g.tabs.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={page === t.id} className={page === t.id ? "active" : ""} onClick={() => go(t.id)}>
            {t.label}
            {counts[t.id] !== undefined && <span className={t.id === "publicacoes" ? "st-tab-count hot" : "st-tab-count"}>{counts[t.id]}</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

function MoneyChips({ go }: { go: (id: string) => void }) {
  const { status } = useSalesStatus();
  if (!status) return null;
  const brl = (c: number) => (c / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  const chips: { text: string; to: string; hot?: boolean }[] = [{ text: `${brl(status.sales.total30)} em 30 dias`, to: "financeiro" }];
  chips.push({ text: `${status.vipMembers.length} no VIP`, to: "assinaturas" });
  if (status.promo?.active) chips.push({ text: `🔥 ${status.promo.label} valendo`, to: "planos", hot: true });
  if (status.sales.pending) chips.push({ text: `${status.sales.pending} Pix esperando`, to: "vendas", hot: true });
  if (!status.pix.configured) chips.push({ text: "Pix desligado", to: "vendas", hot: true });
  return (
    <div className="st-group-chips">
      {chips.map((c) => (
        <button key={c.text} type="button" className={c.hot ? "hot" : ""} onClick={() => go(c.to)}>
          {c.text}
        </button>
      ))}
    </div>
  );
}
