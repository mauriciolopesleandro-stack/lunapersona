import { useEffect, useState } from "react";
import { animateImage, talkVideo, type GenerationImage, type VideoResponse } from "../api/client";
import { downloadFile } from "../lib/download";
import { loadLastVideo, saveLastVideo } from "../lib/lastResult";

interface Props {
  image: GenerationImage;
  // Persona da imagem: "Falando" usa a voz dela (sem persona, so movimento).
  personaId: string | null;
}

const DURATIONS = [5, 10, 15, 20];
// Fala media em portugues: ~15 caracteres por segundo.
const CHARS_PER_SECOND = 15;

// Anima a imagem gerada (Wan 2.2 no pod). O video parte da propria foto,
// entao a persona continua a mesma. Dois modos: movimento (o texto descreve o
// que ela faz) ou falando (o texto e o que ela fala, com a voz dela e a boca
// sincronizada).
export function AnimatePanel({ image, personaId }: Props) {
  const [mode, setMode] = useState<"motion" | "talk">("motion");
  const [motion, setMotion] = useState("");
  const [speech, setSpeech] = useState("");
  const [gesture, setGesture] = useState("");
  const [seconds, setSeconds] = useState(5);
  const [quality, setQuality] = useState<"480p" | "720p">("480p");
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [video, setVideo] = useState<VideoResponse | null>(null);
  const [nextMotion, setNextMotion] = useState("");
  const [nextSeconds, setNextSeconds] = useState(5);

  useEffect(() => {
    setVideo(loadLastVideo(image.url));
    setError(null);
    const img = new Image();
    img.onload = () => setSize({ width: img.naturalWidth, height: img.naturalHeight });
    img.src = image.url;
  }, [image.url]);

  const talking = mode === "talk" && !!personaId;

  async function handleAnimate() {
    setLoading(true);
    setError(null);
    setVideo(null);
    const source = {
      image: image.filename,
      image_subfolder: image.subfolder,
      image_type: (image.type === "input" ? "input" : "output") as "input" | "output",
      quality,
      source_width: size?.width,
      source_height: size?.height,
    };
    try {
      const res = talking
        ? await talkVideo({ ...source, persona_id: personaId!, text: speech.trim(), extra_prompt: gesture.trim() })
        : await animateImage({ ...source, prompt: motion.trim(), seconds });
      setVideo(res);
      saveLastVideo(image.url, res);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }

  // Continuar a historia: o novo trecho parte do ultimo quadro (PNG) e o
  // resultado e o video anterior + o novo, num mp4 so.
  async function handleContinue() {
    if (!video?.videos[0] || !video.last_frame) return;
    setLoading(true);
    setError(null);
    try {
      const res = await animateImage({
        image: image.filename,
        prompt: nextMotion.trim(),
        seconds: nextSeconds,
        quality,
        continue_video: video.videos[0].filename,
        continue_last_frame: video.last_frame.filename,
        continue_width: video.width,
        continue_height: video.height,
        continue_seconds: video.seconds,
      });
      setVideo(res);
      saveLastVideo(image.url, res);
      setNextMotion("");
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }

  const clip = video?.videos[0];
  const talkSeconds = Math.max(5, Math.ceil(speech.trim().length / CHARS_PER_SECOND));
  const minutes = talking
    ? (quality === "720p" ? 20 : 10) * Math.ceil(talkSeconds / 5)
    : (quality === "720p" ? 5 : 3) * (seconds / 5);

  return (
    <div className="panel animate-panel">
      <h3>🎬 Animar esta foto</h3>
      {personaId && (
        <div className="animate-modes" role="tablist">
          <button type="button" className={mode === "motion" ? "active" : ""} onClick={() => setMode("motion")}>
            Movimento
          </button>
          <button type="button" className={mode === "talk" ? "active" : ""} onClick={() => setMode("talk")}>
            🗣 Falando
          </button>
        </div>
      )}
      {talking ? (
        <>
          <textarea
            rows={3}
            value={speech}
            onChange={(e) => setSpeech(e.target.value)}
            maxLength={450}
            placeholder="O que ela fala (com a voz dela). Ex: Gente, acabei de chegar na praia e o dia está perfeito!"
          />
          <input
            type="text"
            value={gesture}
            onChange={(e) => setGesture(e.target.value)}
            placeholder="Opcional: expressão ou gesto. Ex: sorrindo, mexendo no cabelo"
          />
          <p className="muted small">~{talkSeconds} s de fala (até ~29 s).</p>
        </>
      ) : (
        <textarea
          rows={2}
          value={motion}
          onChange={(e) => setMotion(e.target.value)}
          placeholder="O que ela faz no vídeo. Ex: ela sorri, joga o cabelo para o lado e caminha em direção à câmera"
        />
      )}
      <div className="animate-options">
        {!talking && (
          <label>
            Duração
            <select value={seconds} onChange={(e) => setSeconds(Number(e.target.value))}>
              {DURATIONS.map((d) => (
                <option key={d} value={d}>
                  {d} segundos
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          Qualidade
          <select value={quality} onChange={(e) => setQuality(e.target.value as "480p" | "720p")}>
            <option value="480p">480p (mais rápido)</option>
            <option value="720p">720p</option>
          </select>
        </label>
      </div>
      <button
        type="button"
        className="primary"
        onClick={handleAnimate}
        disabled={loading || (talking && !speech.trim())}
      >
        {loading ? "Gerando vídeo..." : talking ? "Gerar vídeo falando" : "Gerar vídeo"}
      </button>
      {loading && <p className="muted small">Leva uns {minutes} minutos. Pode deixar a página aberta.</p>}
      {error && <p className="error small">{error}</p>}
      {clip && (
        <div className="animate-result">
          <video src={clip.url} controls autoPlay loop playsInline />
          <button type="button" className="result-download" onClick={() => downloadFile(clip.url, clip.filename)}>
            ⬇ Baixar vídeo
          </button>
        </div>
      )}
      {clip && video?.last_frame && (
        <div className="animate-continue">
          <h4>➕ Continuar a história ({video.seconds} s até agora)</h4>
          <textarea
            rows={2}
            value={nextMotion}
            onChange={(e) => setNextMotion(e.target.value)}
            placeholder="O que acontece depois. Ex: ela termina o sorvete, joga o guardanapo no lixo e sai andando"
          />
          <div className="animate-options">
            <label>
              Mais
              <select value={nextSeconds} onChange={(e) => setNextSeconds(Number(e.target.value))}>
                {DURATIONS.map((d) => (
                  <option key={d} value={d}>
                    {d} segundos
                  </option>
                ))}
              </select>
            </label>
          </div>
          <button type="button" className="primary" onClick={handleContinue} disabled={loading || !nextMotion.trim()}>
            {loading ? "Continuando..." : "Continuar vídeo"}
          </button>
        </div>
      )}
    </div>
  );
}
