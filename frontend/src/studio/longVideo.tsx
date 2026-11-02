import { useEffect, useMemo, useRef, useState } from "react";
import { CUT_SECONDS, longCut } from "../../api/_outfits";
import { putSignedUpload } from "./cloud";
import { OUTFITS, videoSeconds } from "./dance";
import { ADULT_DESTS } from "./quickPhoto";

// Video longo +18 (pedido de 31/10): no Chat +18, o video mandado (📎) vira a
// Luna no video inteiro. O robo do PC corta em trechos de 5 s (onde a luna se
// perde menos), faz o Copiar movimento em cada um e emenda tudo com o som do
// video original. So para o canal +18 e os pacotes. O custo aparece antes e so
// comeca depois de confirmar. Da para usar so um pedaco do video (02/11, o
// mesmo do bot do Telegram): 5/10/15 s ou do segundo X ao Y.

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
  // pedaco do video: tamanho (0 = completo) e o segundo em que comeca
  const [piece, setPiece] = useState(0);
  const [from, setFrom] = useState(0);
  const [to, setTo] = useState(0);
  const preview = useRef<HTMLVideoElement>(null);
  const previewUrl = useMemo(() => (file ? URL.createObjectURL(file) : ""), [file]);
  useEffect(() => () => URL.revokeObjectURL(previewUrl), [previewUrl]);
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (file) void videoSeconds(file).then(setSeconds);
  }, [file]);

  if (!file) return <p className="st-hint">Para fazer o vídeo longo, mande o arquivo de novo (o vídeo não fica guardado depois de recarregar a página).</p>;
  const full = seconds ? segmentsOf(seconds) : 0;
  const cut = seconds && piece ? longCut(seconds, from, piece) : undefined;
  const segments = cut ? cut.seconds / SEGMENT : full;
  const credits = segments * (SEGMENT + 1);
  const pieces = CUT_SECONDS.filter((s) => s / SEGMENT < full);
  const pick = (size: number, start = from) => {
    setPiece(size);
    setFrom(start);
    setTo(start + size);
  };
  // "do segundo X ao Y": mudar o comeco leva o pedaco junto; mudar o fim muda o
  // tamanho (arredonda para trechos de 5 s)
  const setStart = (a: number) => {
    setFrom(a);
    setTo(a + piece);
  };
  const setEnd = (b: number) => {
    setTo(b);
    if (b > from) setPiece(b - from);
  };
  const chosen = outfit === OTHER ? other.trim() : outfit;
  const tooShort = seconds !== null && seconds < SEGMENT;
  const tooLong = seconds !== null && seconds > MAX_SECONDS;
  const tooBig = file.size > MAX_BYTES;
  const toggle = (k: string) => setAdult(adult.includes(k) ? adult.filter((x) => x !== k) : [...adult, k]);

  async function queue() {
    if (!file || !seconds) return;
    if (!window.confirm(`Fazer o vídeo longo?\n\n${segments} trecho(s) de ${SEGMENT} s${cut ? ` (do ${cut.from} ao ${cut.from + cut.seconds} s do vídeo)` : ""} = ${credits} créditos da Naiaflow.`)) return;
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
        ...(cut ? { cut_from: cut.from, cut_seconds: cut.seconds } : {}),
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
        Vídeo longo na fila do robô ({segments} trecho(s){cut ? `, do ${cut.from} ao ${cut.from + cut.seconds} s` : ""}, {credits} créditos{chosen ? `, roupa ${chosen}` : ""}). Ele chega no Telegram para você aprovar quando o
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
          : `Vídeo de ${seconds.toFixed(1).replace(".", ",")} s → ${segments} trecho(s) de ${SEGMENT} s${cut ? ` (do ${cut.from} ao ${cut.from + cut.seconds} s)` : ""} = ${credits} créditos. O robô faz a Luna em cada trecho e junta tudo com o som do vídeo original.`}
      </p>
      {seconds !== null && pieces.length > 0 && !tooShort && !tooLong && (
        <div className="st-field">
          <span>Quanto do vídeo</span>
          <div className="st-quick-slots" role="group" aria-label="Quanto do vídeo">
            <button type="button" aria-pressed={!cut} className={!cut ? "on" : ""} onClick={() => setPiece(0)}>
              {!cut ? "✓ " : ""}Completo ({Math.round(seconds)} s · {full * (SEGMENT + 1)} créd)
            </button>
            {pieces.map((s) => (
              <button key={s} type="button" aria-pressed={cut?.seconds === s} className={cut?.seconds === s ? "on" : ""} onClick={() => pick(s, cut?.from ?? 0)}>
                {cut?.seconds === s ? "✓ " : ""}
                {s} s · {(s / SEGMENT) * (SEGMENT + 1)} créd
              </button>
            ))}
          </div>
          {piece > 0 && (
            <div className="st-long-cut">
              <video ref={preview} src={previewUrl} controls muted playsInline preload="metadata" />
              <div className="st-row">
                <label>
                  Do segundo{" "}
                  <input className="st-input" type="number" min={0} max={Math.floor(seconds)} value={from} onChange={(e) => setStart(Number(e.target.value) || 0)} />
                </label>
                <label>
                  ao{" "}
                  <input className="st-input" type="number" min={1} max={Math.ceil(seconds)} value={to} onChange={(e) => setEnd(Number(e.target.value) || 0)} />
                </label>
                <button type="button" className="st-btn st-btn-small" onClick={() => pick(cut?.seconds ?? piece, Math.floor(preview.current?.currentTime ?? 0))}>
                  Começar onde o vídeo parou
                </button>
              </div>
              <p className="st-hint">
                {cut
                  ? `Vai usar do ${cut.from} ao ${cut.from + cut.seconds} s (em trechos de ${SEGMENT} s; se passar do fim, começa antes).`
                  : "Esse pedaço é o vídeo inteiro: vai o completo."}
              </p>
            </div>
          )}
        </div>
      )}
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
