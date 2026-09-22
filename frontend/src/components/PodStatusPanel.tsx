import { useEffect, useRef, useState } from "react";
import { getPodStatus, type PodStatus } from "../api/client";

const POLL_INTERVAL_MS = 15_000;

function formatUSD(value: number): string {
  return `$${value.toFixed(4)}`;
}

function formatUptime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
}

export function PodStatusPanel() {
  const [status, setStatus] = useState<PodStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [liveUptime, setLiveUptime] = useState(0);
  const lastFetchAt = useRef<number>(Date.now());

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const data = await getPodStatus();
        if (cancelled) return;
        setStatus(data);
        setLiveUptime(data.uptimeSeconds);
        lastFetchAt.current = Date.now();
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    }

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // Entre um poll e outro, avanca o cronometro localmente para o gasto
  // parecer "ao vivo" sem precisar bater na API a cada segundo.
  useEffect(() => {
    if (!status?.running) return;
    const tick = setInterval(() => {
      setLiveUptime((u) => u + 1);
    }, 1000);
    return () => clearInterval(tick);
  }, [status?.running]);

  if (error) {
    return (
      <div className="pod-status pod-status-error">
        <span className="muted small">Status do pod indisponivel: {error}</span>
      </div>
    );
  }

  if (!status) {
    return <div className="pod-status muted small">Carregando status do pod...</div>;
  }

  const liveSpend = status.running ? (liveUptime / 3600) * status.costPerHr : status.liveSpend;

  return (
    <div className="pod-status">
      <span className={status.running ? "pod-badge on" : "pod-badge off"}>
        {status.running ? "Pod ligado" : "Pod desligado"}
      </span>
      <span className="pod-stat">
        Saldo: <strong>{formatUSD(status.balance)}</strong>
      </span>
      {status.running && (
        <>
          <span className="pod-stat">
            Sessao: <strong>{formatUptime(liveUptime)}</strong>
          </span>
          <span className="pod-stat">
            Gasto na sessao: <strong>{formatUSD(liveSpend)}</strong> ({formatUSD(status.costPerHr)}/h)
          </span>
        </>
      )}
    </div>
  );
}
