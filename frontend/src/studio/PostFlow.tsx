import { useState } from "react";
import { Icon } from "./Icon";
import { NETWORKS } from "./shared";
import { useStudio, type Post, type PostAction, type PostStatus } from "./store";

export const POST_STATUS: Record<PostStatus, string> = {
  rascunho: "Rascunho",
  aguardando_aprovacao: "Aguardando aprovação",
  aprovado: "Aprovado",
  agendado: "Agendado",
  publicando: "Hora de publicar",
  publicado: "Publicado",
  falhou: "Falhou",
  cancelado: "Cancelado",
};

export function PostStatusBadge({ status }: { status: PostStatus }) {
  return <span className={`st-badge st-post-${status}`}>{POST_STATUS[status]}</span>;
}

function fmt(iso: string | null | undefined) {
  return iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
}

function localInput(iso: string | null | undefined) {
  const d = iso ? new Date(iso) : new Date(Date.now() + 3600_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// Fluxo de uma publicacao no modo banco: aprovacao da versao, agendamento,
// lembrete, confirmacao destino a destino, falha e cancelamento.
export function PostFlow({ postId, onClose }: { postId: string; onClose: () => void }) {
  const { posts, postAction } = useStudio();
  const post = posts.find((p) => p.id === postId);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [when, setWhen] = useState(localInput(post?.scheduledAt));
  const [confirming, setConfirming] = useState<Record<string, { checked: boolean; url: string }>>({});
  const [reason, setReason] = useState("");
  if (!post) return null;
  const pending = (post.destinations ?? []).filter((d) => d.status === "pendente");
  const job = post.jobs?.find((j) => j.status === "pendente" || j.status === "executando");
  const failedJob = post.jobs?.find((j) => j.status === "falhou");

  async function run(action: PostAction, extra: object = {}, done?: string, close = false) {
    setBusy(true);
    setMessage(null);
    try {
      await postAction(action, post!.id, extra);
      setMessage({ ok: true, text: done ?? "Feito." });
      if (close) onClose();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  function confirmPublished() {
    const chosen = pending.filter((d) => confirming[d.format]?.checked);
    if (!chosen.length) return setMessage({ ok: false, text: "Marque o que você já publicou." });
    run(
      "confirm",
      { destinations: chosen.map((d) => ({ format: d.format, url: confirming[d.format]?.url || null })) },
      chosen.length === pending.length ? "Publicação confirmada." : "Destinos confirmados; ainda falta o resto."
    );
  }

  const canConfirm = ["aprovado", "agendado", "publicando"].includes(post.status);

  return (
    <div className="st-flow">
      <div className="st-flow-head">
        <PostStatusBadge status={post.status} />
        <span>
          Versão {post.version}
          {post.approvedVersion ? (post.approvedVersion === post.version ? " · aprovada" : ` · aprovada a v${post.approvedVersion} (mudou depois)`) : " · sem aprovação"}
        </span>
        <span>{NETWORKS[post.network].label} · {post.formats.join(", ") || "sem destino"}</span>
      </div>

      {post.status === "aguardando_aprovacao" && (
        <div className="st-flow-box">
          <p>
            Revise mídia, legenda, hashtags e destinos. A aprovação vale só para a <b>versão {post.version}</b>: se algo mudar depois,
            ela volta para aprovação.
          </p>
          <div className="st-head-actions">
            <button
              type="button"
              className="st-btn st-btn-primary"
              disabled={busy}
              onClick={() =>
                run("approve", { version: post.version }, post.scheduledAt ? `Aprovada e agendada para ${fmt(post.scheduledAt)}.` : "Aprovada.")
              }
            >
              <Icon name="check" size={16} />
              Aprovar versão {post.version}
              {post.scheduledAt ? " e agendar" : ""}
            </button>
            <button
              type="button"
              className="st-btn"
              disabled={busy}
              onClick={() => {
                const note = window.prompt("Motivo da reprovação (opcional):") ?? "";
                run("reject", { version: post.version, note }, "Reprovada: voltou para rascunho.");
              }}
            >
              Reprovar
            </button>
          </div>
        </div>
      )}

      {(post.status === "aprovado" || post.status === "agendado") && (
        <div className="st-flow-box">
          {post.status === "agendado" ? (
            <p>
              Agendada para <b>{fmt(post.scheduledAt)}</b>. No horário, o estúdio avisa no Telegram para você publicar.
              {job && ` Lembrete: ${job.status === "pendente" ? "na fila" : "enviando"}${job.attempts ? ` (tentativa ${job.attempts} de ${job.maxAttempts})` : ""}.`}
              {job?.lastError && <span className="st-error"> Último erro: {job.lastError}</span>}
            </p>
          ) : (
            <p>Aprovada. Escolha quando publicar para receber o lembrete no horário.</p>
          )}
          <div className="st-row">
            <input type="datetime-local" className="st-input" value={when} onChange={(e) => setWhen(e.target.value)} aria-label="Data e hora" />
            <button
              type="button"
              className="st-btn"
              disabled={busy}
              onClick={() => run("schedule", { scheduled_at: new Date(when).toISOString() }, `Agendada para ${fmt(new Date(when).toISOString())}.`)}
            >
              <Icon name="calendar" size={16} />
              {post.status === "agendado" ? "Remarcar" : "Agendar"}
            </button>
          </div>
        </div>
      )}

      {failedJob && post.status !== "publicado" && post.status !== "cancelado" && (
        <p className="st-error">O lembrete do Telegram não saiu depois de {failedJob.attempts} tentativas: {failedJob.lastError}</p>
      )}

      {canConfirm && pending.length > 0 && (
        <div className={`st-flow-box${post.status === "publicando" ? " st-flow-alert" : ""}`}>
          <p>
            {post.status === "publicando" ? <b>Chegou a hora. </b> : null}
            Publique pelo app do {NETWORKS[post.network].label} e confirme aqui cada destino. O estúdio só marca como publicado o que você
            confirmar.
          </p>
          {pending.map((d) => (
            <div key={d.format} className="st-row st-confirm-row">
              <label className="st-check">
                <input
                  type="checkbox"
                  checked={confirming[d.format]?.checked ?? false}
                  onChange={(e) => setConfirming((c) => ({ ...c, [d.format]: { checked: e.target.checked, url: c[d.format]?.url ?? "" } }))}
                />
                {d.format}
              </label>
              <input
                className="st-input"
                placeholder="Link do post (opcional, https://...)"
                value={confirming[d.format]?.url ?? ""}
                onChange={(e) => setConfirming((c) => ({ ...c, [d.format]: { checked: true, url: e.target.value } }))}
              />
            </div>
          ))}
          <div className="st-head-actions">
            <button type="button" className="st-btn st-btn-primary" disabled={busy} onClick={confirmPublished}>
              <Icon name="check" size={16} />
              Confirmar publicação
            </button>
            <input className="st-input" placeholder="Por que não deu para publicar?" value={reason} onChange={(e) => setReason(e.target.value)} />
            <button type="button" className="st-btn" disabled={busy} onClick={() => run("fail", { reason }, "Marcada como falha.")}>
              Não deu para publicar
            </button>
          </div>
        </div>
      )}

      {post.status === "falhou" && (
        <div className="st-flow-box">
          <p className="st-error">Falhou: {post.failureReason}</p>
          <p>Ajuste o que precisar acima e envie para aprovação de novo.</p>
          <button type="button" className="st-btn st-btn-primary" disabled={busy} onClick={() => run("submit", {}, "Enviada para aprovação.")}>
            Enviar para aprovação
          </button>
        </div>
      )}

      {post.status === "publicado" && (
        <div className="st-flow-box">
          <p>Publicada em {fmt(post.publishedAt)}.</p>
          <ul className="st-history">
            {(post.destinations ?? [])
              .filter((d) => d.status === "publicado")
              .map((d) => (
                <li key={d.format}>
                  <time>{fmt(d.confirmedAt)}</time>
                  <span>
                    {d.format} · {d.confirmedBy === "importado" ? "marcado antes da Fase 2" : "confirmado por você"}
                    {d.url && (
                      <>
                        {" · "}
                        <a href={d.url} target="_blank" rel="noreferrer">
                          ver post
                        </a>
                      </>
                    )}
                  </span>
                </li>
              ))}
          </ul>
        </div>
      )}

      {message && <p className={message.ok ? "st-ok" : "st-error"}>{message.text}</p>}

      {!["publicado", "cancelado"].includes(post.status) && (
        <button
          type="button"
          className="st-btn st-btn-danger st-flow-cancel"
          disabled={busy}
          onClick={() => window.confirm("Cancelar esta publicação? Ela fica no histórico como cancelada.") && run("cancel", {}, "Cancelada.")}
        >
          Cancelar publicação
        </button>
      )}

      <details className="st-flow-history">
        <summary>Histórico</summary>
        <ul className="st-history">
          {[
            ...(post.approvals ?? []).map((a) => ({ at: a.at, text: `v${a.version} ${a.decision}${a.note ? ` · ${a.note}` : ""}` })),
            ...(post.attempts ?? []).map((a) => ({
              at: a.at,
              text: `${a.channel === "lembrete_telegram" ? "lembrete no Telegram" : a.channel === "manual" ? "confirmação manual" : a.channel}: ${a.outcome === "ok" ? "ok" : "erro"}${a.detail ? ` · ${a.detail}` : ""}`,
            })),
          ]
            .sort((a, b) => (a.at < b.at ? 1 : -1))
            .map((h, i) => (
              <li key={i}>
                <time>{fmt(h.at)}</time>
                <span>{h.text}</span>
              </li>
            ))}
        </ul>
      </details>
    </div>
  );
}
