import { useEffect, useRef, useState } from "react";
import { ProfilePage } from "../components/ProfilePage";
import { Composer } from "./Composer";
import { Icon } from "./Icon";
import { CalendarPage, CharactersPage, ContentsPage, ItemDetails, OverviewPage, PublicationsPage, SettingsPage, type Nav } from "./pages";
import { ComingSoon, Modal, formatBytes } from "./shared";
import { InstagramPage, TikTokPage } from "./social";
import { StudioProvider, useStudio, type Post } from "./store";
import "./studio.css";

const NAV: { id: string; label: string; icon: string }[] = [
  { id: "visao", label: "Visão Geral", icon: "home" },
  { id: "conteudos", label: "Conteúdos", icon: "grid" },
  { id: "calendario", label: "Calendário", icon: "calendar" },
  { id: "publicacoes", label: "Publicações", icon: "send" },
  { id: "instagram", label: "Instagram", icon: "instagram" },
  { id: "tiktok", label: "TikTok", icon: "tiktok" },
  { id: "telegram", label: "Telegram", icon: "telegram" },
  { id: "monetizacao", label: "Monetização", icon: "dollar" },
  { id: "assinaturas", label: "Assinaturas", icon: "subscription" },
  { id: "vendas", label: "Vendas Avulsas", icon: "bag" },
  { id: "audiencia", label: "Audiência", icon: "chart" },
  { id: "financeiro", label: "Financeiro", icon: "wallet" },
  { id: "personagens", label: "Personagens", icon: "users" },
  { id: "configuracoes", label: "Configurações", icon: "gear" },
];

// Modulos que dependem de integracoes oficiais ou de dados que ainda nao existem.
const SOON: Record<string, string> = {
  telegram: "Bot para entregar conteúdo, assinaturas e vendas. O bot de avisos que já existe (Meu perfil) pode ser a base.",
  monetizacao: "Resumo de assinaturas e vendas avulsas, quando o bot do Telegram estiver vendendo.",
  assinaturas: "Planos e assinantes do canal pago no Telegram.",
  vendas: "Packs e conteúdos vendidos avulsos pelo bot.",
  audiencia: "Seguidores e alcance, vindos das integrações oficiais.",
  financeiro: "Receitas e custos do mês, a partir das vendas registradas.",
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
  const [page, setPage] = useState("visao");
  const [menuOpen, setMenuOpen] = useState(false);
  const [userMenu, setUserMenu] = useState(false);
  const [query, setQuery] = useState("");
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [composer, setComposer] = useState<{ post?: Post; date?: Date; media?: string[] } | null>(null);
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
    setPage(id);
    setMenuOpen(false);
    window.scrollTo(0, 0);
  };

  const nav: Nav = {
    openItem: setOpenPath,
    openComposer: setComposer,
    go,
    pickFiles: () => {
      if (studio.state === "ready") fileInput.current?.click();
      else go("conteudos");
    },
  };

  const current = NAV.find((n) => n.id === page);

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
            <button key={n.id} type="button" className={page === n.id ? "active" : ""} onClick={() => go(n.id)}>
              <Icon name={n.icon} />
              {n.label}
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
          {page === "visao" && <OverviewPage nav={nav} />}
          {page === "conteudos" && <ContentsPage nav={nav} query={query} />}
          {page === "calendario" && <CalendarPage nav={nav} />}
          {page === "publicacoes" && <PublicationsPage nav={nav} />}
          {page === "configuracoes" && <SettingsPage />}
          {page === "personagens" && <CharactersPage />}
          {page === "instagram" && <InstagramPage />}
          {page === "tiktok" && <TikTokPage />}
          {page === "perfil" && <ProfilePage onLogout={onLogout} />}
          {SOON[page] && current && <ComingSoon title={current.label} icon={current.icon} text={SOON[page]} />}
        </main>
      </div>

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
          <Composer
            initial={composer.post}
            defaultDate={composer.date}
            defaultMedia={composer.media}
            onDone={() => setComposer(null)}
          />
        </Modal>
      )}
    </div>
  );
}
