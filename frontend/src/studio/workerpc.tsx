import { useEffect, useState } from "react";
import { Icon } from "./Icon";

// Automacao do PC (parte B): o programa da pasta worker/ roda no computador do
// administrador, gera as fotos dos planos aprovados na Naiaflow e manda para o
// bot do Telegram. Aqui: estado (ultimo contato, fila) e "Conectar este
// computador", que baixa o arquivo de configuracao com a chave do PC (a chave
// anterior deixa de valer). Ninguem precisa copiar a chave a mao.

interface Status {
  paired: boolean;
  lastSeen: string | null;
  queue: number;
}

export function WorkerPcPanel() {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function load() {
    const r = await fetch("/api/studio?r=worker");
    if (r.ok) setStatus(await r.json());
  }
  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  async function pair() {
    if (status?.paired && !window.confirm("Gerar uma chave nova? O computador que já está conectado para de funcionar até receber o arquivo novo.")) return;
    setBusy(true);
    setMessage(null);
    try {
      const r = await fetch("/api/studio?r=worker", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "pair" }) });
      const b = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(b.detail ?? `Erro HTTP ${r.status}`);
      const url = URL.createObjectURL(new Blob([b.config], { type: "text/plain" }));
      const a = Object.assign(document.createElement("a"), { href: url, download: "luna-worker.env" });
      document.body.append(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setMessage({ ok: true, text: "Arquivo luna-worker.env baixado. Coloque na pasta worker do estúdio (veja os passos abaixo)." });
      await load();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  const seen = status?.lastSeen ? new Date(status.lastSeen) : null;
  const online = seen ? Date.now() - seen.getTime() < 5 * 60_000 : false;
  return (
    <section className="st-panel st-settings">
      <h2>Automação do PC (Naiaflow)</h2>
      <p className="st-hint">
        Um programa no seu computador gera na Naiaflow as fotos dos planos aprovados (Assistente → Planejamento), salva na pasta do estúdio e manda cada uma para você aprovar
        no Telegram. O computador precisa estar ligado com o programa aberto.
      </p>
      {status && (
        <p>
          <span className={`st-badge ${online ? "st-badge-aprovado" : "st-badge-rascunho"}`}>{online ? "Ligado" : "Desligado"}</span>{" "}
          {seen ? `último contato ${seen.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}` : "nunca conectou"} · {status.queue} foto(s) na fila
        </p>
      )}
      <ol className="st-hint">
        <li>Clique em “Conectar este computador”: baixa o arquivo luna-worker.env.</li>
        <li>Mova o arquivo para a pasta worker, dentro da pasta do estúdio (luna persona\worker).</li>
        <li>Dê dois cliques em “Iniciar automação.bat”, na mesma pasta. Na primeira vez, entre na Naiaflow na janela que abrir.</li>
      </ol>
      <div className="st-head-actions">
        <button type="button" className="st-btn st-btn-primary" disabled={busy} onClick={pair}>
          <Icon name="refresh" size={16} />
          {status?.paired ? "Gerar chave nova para o computador" : "Conectar este computador"}
        </button>
      </div>
      {message && <p className={message.ok ? "st-ok" : "st-error"}>{message.text}</p>}
    </section>
  );
}
