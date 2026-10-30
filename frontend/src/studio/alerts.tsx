import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { POST_STATUS } from "./PostFlow";
import type { Nav } from "./pages";
import { NETWORKS } from "./shared";
import { useStudio, type Post } from "./store";

// Alertas operacionais no sino do topo (pedido de 30/10: na Visao Geral o
// painel empurrava o calendario). O numero mostra os alertas nao limpos; "Limpar"
// esconde os de agora neste navegador e um alerta novo volta a aparecer.

interface Alert {
  key: string;
  text: string;
  post?: Post;
  action?: () => void;
}

const CLEARED_KEY = "luna:alertas-limpos";

function readCleared(): string[] {
  try {
    const list = JSON.parse(localStorage.getItem(CLEARED_KEY) ?? "[]");
    return Array.isArray(list) ? list.filter((k) => typeof k === "string") : [];
  } catch {
    return [];
  }
}

function writeCleared(keys: string[]) {
  try {
    localStorage.setItem(CLEARED_KEY, JSON.stringify(keys.slice(-300)));
  } catch {
    // navegador sem armazenamento: limpa so ate recarregar
  }
}

function useAlerts(nav: Nav): Alert[] {
  const { posts, source, worker, postsError } = useStudio();
  const now = Date.now();
  const alerts: Alert[] = [];
  if (postsError) alerts.push({ key: `erro:${postsError}`, text: postsError, action: () => nav.go("publicacoes") });
  if (source !== "banco") return alerts;
  for (const p of posts) {
    if (p.status === "publicando" && p.scheduledAt && now - new Date(p.scheduledAt).getTime() > 3600_000) {
      alerts.push({
        key: `publicando:${p.id}:${p.version}`,
        text: `${POST_STATUS.publicando} desde ${new Date(p.scheduledAt).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}: confirme ou marque a falha.`,
        post: p,
      });
    }
    if (p.status === "falhou") alerts.push({ key: `falhou:${p.id}:${p.version}`, text: `Publicação falhou: ${p.failureReason ?? "sem motivo"}`, post: p });
    const failed = p.jobs?.find((j) => j.status === "falhou");
    if (failed && p.status !== "publicado" && p.status !== "cancelado") {
      alerts.push({ key: `job:${p.id}:${p.version}`, text: `Lembrete do Telegram não saiu: ${failed.lastError ?? ""}`, post: p });
    }
  }
  const due = posts.some((p) => p.status === "agendado" && p.scheduledAt && new Date(p.scheduledAt).getTime() < now - 5 * 60_000);
  const stale = !worker || now - new Date(worker.last_run_at).getTime() > 10 * 60_000;
  if (due && stale) {
    alerts.push({
      key: `agendador:${new Date().toDateString()}`,
      text: "Há publicação agendada que já passou da hora e a automação não rodou: confira o agendador do Supabase.",
      action: () => nav.go("publicacoes"),
    });
  }
  return alerts;
}

export function AlertsBell({ nav }: { nav: Nav }) {
  const all = useAlerts(nav);
  const [cleared, setCleared] = useState<string[]>(readCleared);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const shown = all.filter((a) => !cleared.includes(a.key));

  // fecha clicando fora ou no Esc
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function clear(keys: string[]) {
    const next = [...new Set([...cleared.filter((k) => all.some((a) => a.key === k)), ...keys])];
    setCleared(next);
    writeCleared(next);
  }

  return (
    <div className="st-bell" ref={box}>
      <button type="button" className="st-icon-btn" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-label={shown.length ? `${shown.length} alertas` : "Alertas"}>
        <Icon name="bell" size={18} />
        {shown.length > 0 && <span className="st-bell-count">{shown.length > 9 ? "9+" : shown.length}</span>}
      </button>
      {open && (
        <div className="st-bell-panel" role="dialog" aria-label="Alertas">
          <div className="st-bell-head">
            <b>Alertas</b>
            {shown.length > 0 && (
              <button type="button" className="st-link" onClick={() => clear(shown.map((a) => a.key))}>
                Limpar tudo
              </button>
            )}
          </div>
          {shown.length === 0 ? (
            <p className="st-bell-empty">Nenhum alerta agora 👌</p>
          ) : (
            <ul>
              {shown.map((a) => (
                <li key={a.key}>
                  <button
                    type="button"
                    className="st-bell-item"
                    onClick={() => {
                      setOpen(false);
                      if (a.post) nav.openComposer({ post: a.post });
                      else a.action?.();
                    }}
                  >
                    {a.post && <Icon name={NETWORKS[a.post.network].icon} size={14} />}
                    <span>
                      {a.post ? `${NETWORKS[a.post.network].label} · ` : ""}
                      {a.text}
                    </span>
                  </button>
                  <button type="button" className="st-icon-btn st-bell-x" onClick={() => clear([a.key])} aria-label="Limpar este alerta">
                    <Icon name="close" size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
