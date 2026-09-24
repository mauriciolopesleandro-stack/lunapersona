import { useEffect, useState } from "react";
import { getHealth, getPodStatus, stopPodRequest, type PodStatus } from "../api/client";

const POLL_INTERVAL_MS = 15_000;
const BACKEND_POLL_INTERVAL_MS = 8_000;

function formatUSD(value: number): string {
  return `$${value.toFixed(2)}`;
}

interface Props {
  onWake: () => void;
  waking: boolean;
  theme: "dark" | "light";
  onToggleTheme: () => void;
  onOpenSidebar: () => void;
  onLogout: () => void;
}

export function TopBar({ onWake, waking, theme, onToggleTheme, onOpenSidebar, onLogout }: Props) {
  const [status, setStatus] = useState<PodStatus | null>(null);
  const [error, setError] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  // Pod "rodando" (RunPod) e diferente de "chat pronto" (backend/Ollama de
  // pe dentro do pod) - o Ollama ainda demora a subir depois do pod ligar.
  const [backendReady, setBackendReady] = useState(false);

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

  // So fica de olho no backend enquanto o pod estiver rodando - sem isso
  // fica tentando bater num pod desligado a cada poucos segundos a toa.
  useEffect(() => {
    if (!running) {
      setBackendReady(false);
      return;
    }
    let cancelled = false;

    async function checkBackend() {
      try {
        await getHealth();
        if (!cancelled) setBackendReady(true);
      } catch {
        if (!cancelled) setBackendReady(false);
      }
    }

    checkBackend();
    const interval = setInterval(checkBackend, BACKEND_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [running]);

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
          <span
            className={`pill ${backendReady ? "pill-ready" : "pill-preparing"}`}
            title={backendReady ? "Chat e geracao prontos para usar" : "Backend ainda esta subindo dentro do pod (pode levar 1-2 min)"}
          >
            <span className="pill-dot" />
            <span className="topbar-label-full">{backendReady ? "Chat pronto" : "Preparando chat..."}</span>
            <span className="topbar-label-short">{backendReady ? "Pronto" : "Prep..."}</span>
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

      <button type="button" className="icon-btn" onClick={onLogout} aria-label="Sair" title="Sair">
        ⏏
      </button>
    </div>
  );
}
