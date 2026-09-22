export type Tab = "gerar" | "personas" | "historico" | "galeria" | "configuracoes";

interface Props {
  tab: Tab;
  onTabChange: (tab: Tab) => void;
  open: boolean;
  onClose: () => void;
}

const NAV_ITEMS: { id: Tab; label: string; icon: string }[] = [
  { id: "gerar", label: "Gerar", icon: "✎" },
  { id: "personas", label: "Personas", icon: "◔" },
  { id: "historico", label: "Histórico", icon: "↺" },
  { id: "galeria", label: "Galeria", icon: "▦" },
  { id: "configuracoes", label: "Configurações", icon: "⚙" },
];

export function Sidebar({ tab, onTabChange, open, onClose }: Props) {
  return (
    <aside className={open ? "sidebar open" : "sidebar"}>
      <div className="sidebar-brand">
        <div className="sidebar-brand-mark" aria-hidden="true" />
        <div>
          <div className="sidebar-brand-name">Luna AI Studio</div>
          <div className="sidebar-brand-tag">Imagens que contam histórias</div>
        </div>
      </div>

      <nav className="sidebar-nav">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={tab === item.id ? "active" : ""}
            onClick={() => {
              onTabChange(item.id);
              onClose();
            }}
          >
            <span className="sidebar-nav-icon" aria-hidden="true">
              {item.icon}
            </span>
            {item.label}
          </button>
        ))}
      </nav>

      <div className="sidebar-spacer" />

      <div className="sidebar-footer-card">
        <strong>Custo real de GPU</strong>
        Cada geração usa o pod RunPod ligado. O saldo no topo é o saldo real da conta — acompanhe antes de gerar muito.
      </div>
    </aside>
  );
}
