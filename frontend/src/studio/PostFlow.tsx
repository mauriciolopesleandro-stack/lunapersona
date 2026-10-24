import { useState } from "react";
import { Icon } from "./Icon";
import { NETWORKS } from "./shared";
import { useStudio, type Post, type PostAction, type PostStatus } from "./store";

export const POST_STATUS: Record<PostStatus, string> = {
  rascunho: "Rascunho",
  aguardando_aprovacao: "Aguardando aprovação",
  aprovado: "Aguardando publicação",
  agendado: "Agendada",
  publicando: "Processando",
  publicado: "Publicada",
  falhou: "Falhou",
  cancelado: "Cancelada",
};

// "publicando" e Processando quando a API publica sozinha (Instagram
// automatico); se depende de voce (manual, ou rascunho no app do TikTok), e
// Requer acao manual.
export function postStatusLabel(p: Pick<Post, "status" | "publishMode" | "network">): string {
  if (p.status === "publicando" && !(p.publishMode === "api" && p.network === "instagram")) return "Requer ação manual";
  return POST_STATUS[p.status];
}

export function PostStatusBadge({ status, post }: { status: PostStatus; post?: Pick<Post, "status" | "publishMode" | "network"> }) {
  const label = post ? postStatusLabel(post) : POST_STATUS[status];
  return <span className={`st-badge st-post-${status}${label === "Requer ação manual" ? " st-post-manual" : ""}`}>{label}</span>;
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
  const { posts, postAction, approveAutomatic } = useStudio();
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
  // mesma publicacao em outras redes (grupo) esperando aprovacao
  const siblings = post.groupId ? posts.filter((p) => p.groupId === post.groupId && p.id !== post.id) : [];
  const waiting = [post, ...siblings].filter((p) => p.status === "aguardando_aprovacao");

  // Aprova todas as redes do grupo de uma vez (cada uma na sua versao atual).
  async function approveGroup() {
    setBusy(true);
    setMessage(null);
    const ok: string[] = [];
    try {
      for (const p of waiting) {
        const label = NETWORKS[p.network].label;
        if (p.publishMode === "api") await approveAutomatic(p, (text) => setMessage({ ok: true, text: `${label}: ${text}` }));
        else await postAction("approve", p.id, { version: p.version });
        ok.push(label);
      }
      setMessage({ ok: true, text: `Aprovada em ${ok.join(", ")}.` });
    } catch (e) {
      const err = e instanceof Error ? e.message : String(e);
      setMessage({ ok: false, text: ok.length ? `Aprovada em ${ok.join(", ")}; parou na próxima rede: ${err}` : err });
    } finally {
      setBusy(false);
    }
  }

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

  const automatic = post.publishMode === "api";
  // TikTok automatico = rascunho: quem publica e voce, no app do TikTok
  const tiktokDraft = automatic && post.network === "tiktok";
  // automatico: quem confirma e a API, nao o administrador (no TikTok fica a
  // confirmacao manual como reserva, caso o TikTok nao avise)
  const canConfirm = (!automatic && ["aprovado", "agendado", "publicando"].includes(post.status)) || (tiktokDraft && post.status === "publicando");

  async function approve() {
    if (!automatic) {
      return run("approve", { version: post!.version }, post!.scheduledAt ? `Aprovada e agendada para ${fmt(post!.scheduledAt)}.` : "Aprovada.");
    }
    setBusy(true);
    setMessage(null);
    try {
      await approveAutomatic(post!, (text) => setMessage({ ok: true, text }));
      setMessage({ ok: true, text: post!.scheduledAt
          ? tiktokDraft
            ? `Aprovada: o rascunho vai para o TikTok em ${fmt(post!.scheduledAt)}.`
            : `Aprovada: publica sozinha em ${fmt(post!.scheduledAt)}.`
          : "Aprovada. Escolha o horário para publicar." });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  async function copyCaption() {
    const text = [post!.caption, post!.hashtags.join(" ")].filter(Boolean).join("\n\n");
    try {
      await navigator.clipboard.writeText(text);
      setMessage({ ok: true, text: "Legenda e hashtags copiadas." });
    } catch {
      setMessage({ ok: false, text: "O navegador não deixou copiar. Selecione o texto da legenda acima." });
    }
  }

  return (
    <div className="st-flow">
      <div className="st-flow-head">
        <PostStatusBadge status={post.status} post={post} />
        <span>
          Versão {post.version}
          {post.approvedVersion ? (post.approvedVersion === post.version ? " · aprovada" : ` · aprovada a v${post.approvedVersion} (mudou depois)`) : " · sem aprovação"}
        </span>
        <span>{NETWORKS[post.network].label} · {post.formats.join(", ") || "sem destino"}</span>
        {siblings.length > 0 && <span className="st-badge st-post-rascunho">também em {siblings.map((p) => NETWORKS[p.network].label).join(", ")}</span>}
        <span className={automatic ? "st-badge st-post-agendado" : "st-badge st-post-rascunho"}>{automatic ? "Automática" : "Manual"}</span>
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
              onClick={approve}
            >
              <Icon name="check" size={16} />
              {automatic ? "Enviar mídias e aprovar" : "Aprovar"} versão {post.version}
              {post.scheduledAt ? " e agendar" : ""}
            </button>
            {waiting.length > 1 && (
              <button type="button" className="st-btn st-btn-primary" disabled={busy} onClick={approveGroup}>
                <Icon name="check" size={16} />
                Aprovar todas as redes ({waiting.map((p) => NETWORKS[p.network].label).join(", ")})
              </button>
            )}
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
              Agendada para <b>{fmt(post.scheduledAt)}</b>.{" "}
              {tiktokDraft
                ? "No horário, o estúdio envia o rascunho para o TikTok e te avisa no Telegram."
                : automatic
                ? "No horário, o estúdio publica sozinho no Instagram e te avisa no Telegram."
                : "No horário, o estúdio avisa no Telegram para você publicar."}
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
        <p className="st-error">
          {automatic ? `O envio automático para o ${NETWORKS[post.network].label} falhou` : "O lembrete do Telegram não saiu"} depois de{" "}
          {failedJob.attempts} tentativa(s): {failedJob.lastError}
        </p>
      )}

      {canConfirm && pending.length > 0 && (
        <div className={`st-flow-box${post.status === "publicando" ? " st-flow-alert" : ""}`}>
          <p>
            {tiktokDraft ? (
              <>Se você já publicou o rascunho e o TikTok ainda não avisou o estúdio, pode confirmar aqui.</>
            ) : (
              <>
                {post.status === "publicando" ? <b>Chegou a hora. </b> : null}
                Publique pelo app do {NETWORKS[post.network].label} e confirme aqui cada destino. O estúdio só marca como publicado o que você
                confirmar.
              </>
            )}
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
            <button type="button" className="st-btn" onClick={copyCaption}>
              Copiar legenda e hashtags
            </button>
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

      {automatic && post.status === "publicando" && (
        <div className="st-flow-box st-flow-alert">
          {tiktokDraft ? (
            <p>
              Rascunho enviado para o TikTok. Abra as notificações do app do TikTok, coloque a música e publique; o estúdio marca como publicado
              sozinho quando o TikTok confirmar.
            </p>
          ) : (
            <p>Publicando no Instagram... (vídeos podem levar alguns minutos para processar).</p>
          )}
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
                    {d.format} ·{" "}
                    {d.confirmedBy === "importado" ? "marcado antes da Fase 2" : d.confirmedBy === "api" ? `confirmado pela API do ${NETWORKS[post.network].label}` : "confirmado por você"}
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
