import { GPU_ENABLED } from "../lib/studioMode";

export type Tab = "inicio" | "gerar" | "v2" | "historia" | "pack" |"trocar" | "conteudo" | "voz" | "personas" | "historico" | "galeria" | "configuracoes" | "perfil";

interface Props {
  tab: Tab;
  onTabChange: (tab: Tab) => void;
  open: boolean;
  onClose: () => void;
}

const NAV_ITEMS: { id: Tab; label: string; icon: string }[] = [
  { id: "gerar", label: "Gerar", icon: "✎" },
  { id: "v2", label: "Trocar pessoa", icon: "◈" },
  { id: "historia", label: "História", icon: "❡" },
  { id: "pack", label: "Pack com Luna", icon: "▤" },
  { id: "trocar", label: "Trocar vídeo", icon: "⇄" },
  { id: "conteudo", label: "Conteúdo", icon: "✦" },
  { id: "voz", label: "Voz", icon: "♪" },
  { id: "personas", label: "Personas", icon: "◔" },
  { id: "historico", label: "Histórico", icon: "↺" },
  { id: "galeria", label: "Galeria", icon: "▦" },
  { id: "configuracoes", label: "Configurações", icon: "⚙" },
  { id: "perfil", label: "Meu perfil", icon: "◉" },
];

// Modo manual: so o que funciona sem o pod (ver lib/studioMode.ts).
const MANUAL_NAV_ITEMS: { id: Tab; label: string; icon: string }[] = [
  { id: "inicio", label: "Visão geral", icon: "◇" },
  { id: "perfil", label: "Meu perfil", icon: "◉" },
];

export function Sidebar({ tab, onTabChange, open, onClose }: Props) {
  return (
    <aside className={open ? "sidebar open" : "sidebar"}>
      <div className="sidebar-brand">
        <div className="sidebar-brand-mark" aria-hidden="true" />
        <div>
          <div className="sidebar-brand-name">{GPU_ENABLED ? "Luna AI Studio" : "Luna Vox Studio"}</div>
          <div className="sidebar-brand-tag">{GPU_ENABLED ? "Imagens que contam histórias" : "Conteúdo, agenda e vendas"}</div>
        </div>
      </div>

      <nav className="sidebar-nav">
        {(GPU_ENABLED ? NAV_ITEMS : MANUAL_NAV_ITEMS).map((item) => (
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

      {GPU_ENABLED && (
        <div className="sidebar-footer-card">
          <strong>Custo real de GPU</strong>
          Cada geração usa o pod RunPod ligado. O saldo no topo é o saldo real da conta — acompanhe antes de gerar muito.
        </div>
      )}
    </aside>
  );
}
