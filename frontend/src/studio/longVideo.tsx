import { useEffect, useState } from "react";
import { putSignedUpload } from "./cloud";
import { OUTFITS, videoSeconds } from "./dance";
import { ADULT_DESTS } from "./quickPhoto";

// Video longo +18 (pedido de 31/10): no Chat +18, o video mandado (📎) vira a
// Luna no video inteiro. O robo do PC corta em trechos de 5 s (onde a luna se
// perde menos), faz o Copiar movimento em cada um e emenda tudo com o som do
// video original. So para o canal +18 e os pacotes. O custo aparece antes e so
// comeca depois de confirmar.

const SEGMENT = 5;
const MAX_SECONDS = 300;
const MAX_BYTES = 50 * 1024 * 1024; // limite do envio (api/_plans.ts dance-url)
const KEEP = "";
const OTHER = "__outra__";
// mesma conta do servidor (api/_plans.ts longSegments)
const segmentsOf = (seconds: number) => Math.max(1, Math.ceil(seconds / SEGMENT - 0.02));

async function post<T>(body: object): Promise<T> {
  const r = await fetch("/api/studio?r=plans", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(b.detail ?? `Erro HTTP ${r.status}`);
  return b as T;
}

export function LongVideoCard({ file }: { file?: File }) {
  const [open, setOpen] = useState(false);
  const [seconds, setSeconds] = useState<number | null>(null);
  const [outfit, setOutfit] = useState(KEEP);
  const [other, setOther] = useState("");
  const [adult, setAdult] = useState<string[]>(ADULT_DESTS.map((d) => d.key));
  const [caption, setCaption] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (file) void videoSeconds(file).then(setSeconds);
  }, [file]);

  if (!file) return <p className="st-hint">Para fazer o vídeo longo, mande o arquivo de novo (o vídeo não fica guardado depois de recarregar a página).</p>;
  const segments = seconds ? segmentsOf(seconds) : 0;
  const credits = segments * (SEGMENT + 1);
  const chosen = outfit === OTHER ? other.trim() : outfit;
  const tooShort = seconds !== null && seconds < SEGMENT;
  const tooLong = seconds !== null && seconds > MAX_SECONDS;
  const tooBig = file.size > MAX_BYTES;
  const toggle = (k: string) => setAdult(adult.includes(k) ? adult.filter((x) => x !== k) : [...adult, k]);

  async function queue() {
    if (!file || !seconds) return;
    if (!window.confirm(`Fazer o vídeo longo?\n\n${segments} trechos de ${SEGMENT} s = ${credits} créditos da Naiaflow.`)) return;
    setError(null);
    try {
      setBusy("Enviando o vídeo…");
      const up = await post<{ path: string; url: string }>({ action: "dance-url", mime_type: file.type || "video/mp4", size_bytes: file.size });
      await putSignedUpload(up.url, file);
      setBusy("Mandando para o robô…");
      await post({
        action: "long-video",
        path: up.path,
        duration: seconds,
        credits,
        outfit: chosen,
        caption,
        adultDest: { channel: adult.includes("canal"), packs: adult.filter((k) => k !== "canal") },
        scheduled_at: "auto",
      });
      setDone(true);
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (done) {
    return (
      <p className="st-ok">
        Vídeo longo na fila do robô ({segments} trechos, {credits} créditos{chosen ? `, roupa ${chosen}` : ""}). Ele chega no Telegram para você aprovar quando o
        último trecho ficar pronto — o robô do PC precisa ficar aberto.
      </p>
    );
  }
  if (!open) {
    return (
      <button type="button" className="st-btn st-btn-small st-btn-primary st-dance-open" onClick={() => setOpen(true)}>
        🎬 Fazer vídeo longo com a Luna (+18)
      </button>
    );
  }
  return (
    <div className="st-quick-photo st-dance st-long-video">
      <p className="st-hint">
        {seconds === null
          ? "Lendo a duração do vídeo…"
          : `Vídeo de ${seconds.toFixed(1).replace(".", ",")} s → ${segments} trecho(s) de ${SEGMENT} s = ${credits} créditos. O robô faz a Luna em cada trecho e junta tudo com o som do vídeo original.`}
      </p>
      {tooShort && <p className="st-warn">O vídeo precisa ter pelo menos {SEGMENT} s.</p>}
      {tooLong && <p className="st-warn">O vídeo pode ter no máximo {MAX_SECONDS / 60} minutos: corte antes de mandar.</p>}
      {tooBig && <p className="st-warn">O arquivo tem {Math.round(file.size / 1024 / 1024)} MB e o envio aceita até 50 MB: exporte em resolução menor (720p) antes de mandar.</p>}
      <div className="st-field">
        <span>Onde entra o +18 (no horário)</span>
        <div className="st-quick-slots" role="group" aria-label="Destinos +18">
          {ADULT_DESTS.map((d) => (
            <button key={d.key} type="button" aria-pressed={adult.includes(d.key)} className={adult.includes(d.key) ? "on" : ""} onClick={() => toggle(d.key)}>
              {adult.includes(d.key) ? "✓ " : ""}
              {d.label}
            </button>
          ))}
        </div>
      </div>
      <label className="st-field">
        <span>Roupa da Luna</span>
        <select className="st-input" value={outfit} onChange={(e) => setOutfit(e.target.value)}>
          <option value={KEEP}>Manter a do vídeo</option>
          {OUTFITS.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
          <option value={OTHER}>Outra (nome na galeria)…</option>
        </select>
      </label>
      {outfit === OTHER && (
        <label className="st-field">
          <span>Nome da roupa na galeria da Naiaflow</span>
          <input className="st-input" value={other} onChange={(e) => setOther(e.target.value)} />
        </label>
      )}
      <label className="st-field">
        <span>Legenda do canal (a historinha embaixo do vídeo)</span>
        <textarea className="st-input" rows={4} value={caption} onChange={(e) => setCaption(e.target.value)} />
      </label>
      {error && <p className="st-error">{error}</p>}
      <div className="st-row">
        <button
          type="button"
          className="st-btn st-btn-small st-btn-primary"
          disabled={busy !== null || !seconds || tooShort || tooLong || tooBig || !adult.length || (outfit === OTHER && !other.trim())}
          onClick={queue}
        >
          {busy ?? `Fazer vídeo longo (${credits} créditos)`}
        </button>
        <button type="button" className="st-btn st-btn-small" disabled={busy !== null} onClick={() => setOpen(false)}>
          Cancelar
        </button>
      </div>
    </div>
  );
}
