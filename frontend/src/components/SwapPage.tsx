import { useEffect, useState } from "react";
import { swapVideo, uploadSwapVideo, type PersonaSummary, type VideoResponse } from "../api/client";
import { downloadFile } from "../lib/download";
import { loadLastVideo, saveLastVideo } from "../lib/lastResult";
import { SWAP_MAX_SECONDS, swapSecondsFor, videoDuration } from "../lib/videoDuration";

interface Props {
  personas: PersonaSummary[];
  ensureAwake: () => Promise<unknown>;
}

const LAST_KEY = "trocar-video";

// Aba Trocar video: so o video. A persona e criada sozinha a partir do 1o
// quadro (mesma roupa e pose da pessoa do video) e depois entra no lugar dela.
export function SwapPage({ personas, ensureAwake }: Props) {
  const [personaId, setPersonaId] = useState(personas[0]?.id ?? "");
  const [file, setFile] = useState<File | null>(null);
  // Duracao real do video escolhido: a troca usa ele inteiro.
  const [duration, setDuration] = useState(0);
  const [quality, setQuality] = useState<"480p" | "720p">("480p");
  const [detail, setDetail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [video, setVideo] = useState<VideoResponse | null>(() => loadLastVideo(LAST_KEY));

  useEffect(() => {
    if (!personaId && personas[0]) setPersonaId(personas[0].id);
  }, [personas, personaId]);

  async function handleSwap() {
    if (!file || !personaId) return;
    setLoading(true);
    setError(null);
    setVideo(null);
    try {
      await ensureAwake();
      const uploaded = await uploadSwapVideo(file);
      const res = await swapVideo({
        video: uploaded,
        image: "",
        persona_id: personaId,
        prompt: detail.trim(),
        quality,
        max_seconds: seconds,
      });
      setVideo(res);
      saveLastVideo(LAST_KEY, res);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }

  const personaName = personas.find((p) => p.id === personaId)?.name ?? "a persona";
  const seconds = swapSecondsFor(duration);
  const minutes = (quality === "720p" ? 25 : 12) * Math.ceil(seconds / 5) + 2;
  const clip = video?.videos[0];

  return (
    <div className="voice-page">
      <div className="panel animate-panel">
        <h2>🔁 Trocar pessoa em vídeo</h2>
        <p className="muted small">
          Escolha só o vídeo (seu ou com autorização). O estúdio cria {personaName} com a mesma roupa e pose da pessoa
          do vídeo e troca uma pela outra, mantendo movimento, cenário e áudio. Funciona melhor com uma pessoa só, de
          corpo visível.
        </p>
        {personas.length > 1 && (
          <label className="voice-field">
            Persona
            <select value={personaId} onChange={(e) => setPersonaId(e.target.value)}>
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="reference-upload">
          <input
            type="file"
            accept="video/mp4,video/quicktime,video/webm"
            onChange={(e) => {
              const picked = e.target.files?.[0] ?? null;
              setFile(picked);
              setDuration(0);
              if (picked) videoDuration(picked).then(setDuration);
            }}
          />
          🎞 {file ? file.name : "Escolher vídeo"}
        </label>
        {file && duration > 0 && (
          <p className="muted small">
            {duration > SWAP_MAX_SECONDS
              ? `O vídeo tem ${Math.round(duration)} s: a troca usa os primeiros ${SWAP_MAX_SECONDS} s (máximo).`
              : `Vídeo de ${duration.toFixed(1)} s: a troca usa ele inteiro.`}
          </p>
        )}
        <input
          type="text"
          value={detail}
          onChange={(e) => setDetail(e.target.value)}
          placeholder="Opcional: detalhe da cena. Ex: ela está sorrindo"
        />
        <div className="animate-options">
          <label>
            Qualidade
            <select value={quality} onChange={(e) => setQuality(e.target.value as "480p" | "720p")}>
              <option value="480p">480p (mais rápido)</option>
              <option value="720p">720p</option>
            </select>
          </label>
        </div>
        <button type="button" className="primary" onClick={handleSwap} disabled={loading || !file || !personaId}>
          {loading ? "Trocando..." : `Trocar por ${personaName}`}
        </button>
        {loading && <p className="muted small">Leva uns {minutes} minutos. Pode deixar a página aberta.</p>}
        {error && <p className="error small">{error}</p>}
        {clip && (
          <div className="animate-result">
            <video src={clip.url} controls autoPlay loop playsInline />
            <button type="button" className="result-download" onClick={() => downloadFile(clip.url, clip.filename)}>
              ⬇ Baixar vídeo
            </button>
            {video?.reference && (
              <div className="voice-current">
                <p className="muted small">{personaName} criada a partir do vídeo:</p>
                <img src={video.reference.url} alt="" style={{ maxWidth: 240, borderRadius: 8 }} />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
