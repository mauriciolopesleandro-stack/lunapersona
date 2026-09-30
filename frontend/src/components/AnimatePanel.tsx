import { useEffect, useState } from "react";
import { animateImage, type GenerationImage, type VideoResponse } from "../api/client";
import { downloadFile } from "../lib/download";
import { loadLastVideo, saveLastVideo } from "../lib/lastResult";

interface Props {
  image: GenerationImage;
}

const DURATIONS = [5, 10, 15, 20];

// Anima a imagem gerada (Wan 2.2 no pod). O video parte da propria foto,
// entao a persona continua a mesma; o texto descreve so o movimento.
export function AnimatePanel({ image }: Props) {
  const [motion, setMotion] = useState("");
  const [seconds, setSeconds] = useState(5);
  const [quality, setQuality] = useState<"480p" | "720p">("480p");
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [video, setVideo] = useState<VideoResponse | null>(null);

  useEffect(() => {
    setVideo(loadLastVideo(image.url));
    setError(null);
    const img = new Image();
    img.onload = () => setSize({ width: img.naturalWidth, height: img.naturalHeight });
    img.src = image.url;
  }, [image.url]);

  async function handleAnimate() {
    setLoading(true);
    setError(null);
    setVideo(null);
    try {
      const res = await animateImage({
        image: image.filename,
        image_subfolder: image.subfolder,
        image_type: image.type === "input" ? "input" : "output",
        prompt: motion.trim(),
        seconds,
        quality,
        source_width: size?.width,
        source_height: size?.height,
      });
      setVideo(res);
      saveLastVideo(image.url, res);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }

  const clip = video?.videos[0];
  const minutes = (quality === "720p" ? 5 : 3) * (seconds / 5);

  return (
    <div className="panel animate-panel">
      <h3>🎬 Animar esta foto</h3>
      <textarea
        rows={2}
        value={motion}
        onChange={(e) => setMotion(e.target.value)}
        placeholder="O que ela faz no vídeo. Ex: ela sorri, joga o cabelo para o lado e caminha em direção à câmera"
      />
      <div className="animate-options">
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
        <label>
          Qualidade
          <select value={quality} onChange={(e) => setQuality(e.target.value as "480p" | "720p")}>
            <option value="480p">480p (mais rápido)</option>
            <option value="720p">720p</option>
          </select>
        </label>
      </div>
      <button type="button" className="primary" onClick={handleAnimate} disabled={loading}>
        {loading ? "Gerando vídeo..." : "Gerar vídeo"}
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
    </div>
  );
}
