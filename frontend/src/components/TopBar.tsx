import { useEffect, useState } from "react";
import { getPodStatus, stopPodRequest, type PodStatus } from "../api/client";

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
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);

  async function refreshStatus() {
    try {
      setStatus(await getPodStatus());
      setError(false);
    } catch {
      setError(true);
    }
  }

  async function handleStop() {
    if (!window.confirm("Desligar o pod agora? Chat e geração de imagem param até ligar de novo.")) return;
    setStopping(true);
    setStopError(null);
    try {
      await stopPodRequest();
      await refreshStatus();
    } catch (e) {
      setStopError(e instanceof Error ? e.message : String(e));
    } finally {
      setStopping(false);
    }
  }

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
        <>
          <span className="pill pill-online">
            <span className="pill-dot" />
            <span className="topbar-label-full">Pod Online</span>
            <span className="topbar-label-short">Online</span>
          </span>
          <button
            type="button"
            className="pill pill-stop"
            onClick={handleStop}
            disabled={stopping}
            title={stopError ?? "Desligar o pod para parar a cobrança"}
          >
            <span className="topbar-label-full">
              {stopping ? "Desligando..." : stopError ? "Erro ao desligar · tentar de novo" : "⏻ Desligar"}
            </span>
            <span className="topbar-label-short">{stopping ? "..." : "⏻"}</span>
          </button>
        </>
      ) : (
        <button
          type="button"
          className="pill pill-offline pill-btn"
          onClick={onWake}
          disabled={waking || !status}
          title="Clique para ligar o pod"
        >
          <span className="pill-dot" />
          <span className="topbar-label-full">{waking ? "Ligando..." : "Pod Offline · clique para ligar"}</span>
          <span className="topbar-label-short">{waking ? "..." : "Ligar"}</span>
        </button>
      )}

      <span className="pill pill-credits" title="Saldo real da conta RunPod">
        <span className="topbar-label-full">Créditos: </span>
        {status && !error ? formatUSD(status.balance) : "..."}
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
