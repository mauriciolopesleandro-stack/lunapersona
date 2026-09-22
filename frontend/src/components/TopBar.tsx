import { useEffect, useState } from "react";
import { getPodStatus, type PodStatus } from "../api/client";

const POLL_INTERVAL_MS = 15_000;

function formatUSD(value: number): string {
  return `$${value.toFixed(2)}`;
}

interface Props {
  onWake: () => void;
  waking: boolean;
  theme: "dark" | "light";
  onToggleTheme: () => void;
  onOpenSidebar: () => void;
}

export function TopBar({ onWake, waking, theme, onToggleTheme, onOpenSidebar }: Props) {
  const [status, setStatus] = useState<PodStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const data = await getPodStatus();
        if (!cancelled) {
          setStatus(data);
          setError(false);
        }
      } catch {
        if (!cancelled) setError(true);
      }
    }

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const running = status?.running ?? false;

  return (
    <div className="topbar">
      <button type="button" className="icon-btn topbar-menu-btn" onClick={onOpenSidebar} aria-label="Abrir menu">
        ☰
      </button>

      {running ? (
        <span className="pill pill-online">
          <span className="pill-dot" />
          Pod Online
        </span>
      ) : (
        <button
          type="button"
          className="pill pill-offline pill-btn"
          onClick={onWake}
          disabled={waking || !status}
          title="Clique para ligar o pod"
        >
          <span className="pill-dot" />
          {waking ? "Ligando..." : "Pod Offline · clique para ligar"}
        </button>
      )}

      <span className="pill pill-credits" title="Saldo real da conta RunPod">
        Créditos: {status && !error ? formatUSD(status.balance) : "..."}
      </span>

      <button
        type="button"
        className="icon-btn"
        onClick={onToggleTheme}
        aria-label={theme === "dark" ? "Mudar para tema claro" : "Mudar para tema escuro"}
      >
        {theme === "dark" ? "☀" : "☾"}
      </button>
    </div>
  );
}
