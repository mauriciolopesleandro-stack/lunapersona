import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./Icon";

interface Song {
  rank: number;
  id: string;
  name: string;
  artist: string;
  genres: string[];
  artwork: string;
  url: string;
  releaseDate: string | null;
  previewUrl: string | null;
  durationMs: number | null;
}

interface Chart {
  source: string;
  updated: string;
  copyright: string;
  cachedAt: string;
  stale?: boolean;
  songs: Song[];
}

const TIKTOK_TRENDS = "https://ads.tiktok.com/creative/creativeCenter/trends/music?region=BR&period=7";

function duration(ms: number | null) {
  if (!ms) return "";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// Musicas em alta no Brasil (Apple Music, via /api/studio?r=music) para
// escolher a trilha dos posts. A API do Instagram nao poe musica nos posts
// automaticos: a musica entra pelo app (Reels/Stories) ou nos posts manuais.
export function MusicPage() {
  const [chart, setChart] = useState<Chart | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [genre, setGenre] = useState("");
  const [query, setQuery] = useState("");
  const [playing, setPlaying] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);

  async function load(refresh = false) {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/studio?r=music${refresh ? "&refresh=1" : ""}`);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `Erro HTTP ${res.status}`);
      setChart(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    return () => audio.current?.pause();
  }, []);

  const genres = useMemo(() => {
    const count = new Map<string, number>();
    for (const s of chart?.songs ?? []) for (const g of s.genres) count.set(g, (count.get(g) ?? 0) + 1);
    return [...count.entries()].sort((a, b) => b[1] - a[1]).map(([g]) => g);
  }, [chart]);

  const list = (chart?.songs ?? []).filter((s) => {
    if (genre && !s.genres.includes(genre)) return false;
    const q = query.trim().toLowerCase();
    return !q || `${s.name} ${s.artist}`.toLowerCase().includes(q);
  });

  function toggle(song: Song) {
    if (!song.previewUrl) return;
    if (playing === song.id) {
      audio.current?.pause();
      setPlaying(null);
      return;
    }
    audio.current?.pause();
    const a = new Audio(song.previewUrl);
    a.onended = () => setPlaying(null);
    a.play().catch(() => setPlaying(null));
    audio.current = a;
    setPlaying(song.id);
  }

  async function copy(song: Song) {
    try {
      await navigator.clipboard.writeText(`${song.name} — ${song.artist}`);
      setCopied(song.id);
      setTimeout(() => setCopied((c) => (c === song.id ? null : c)), 1500);
    } catch {
      setError("O navegador não deixou copiar.");
    }
  }

  return (
    <div>
      <div className="st-page-head">
        <div>
          <h1>Músicas em alta</h1>
          <p>
            {chart
              ? `${chart.source} · atualizado em ${new Date(chart.updated).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}`
              : "As mais tocadas no Brasil agora."}
          </p>
        </div>
        <button type="button" className="st-btn" onClick={() => load(true)} disabled={loading}>
          <Icon name="refresh" size={16} />
          Atualizar
        </button>
      </div>

      <div className="st-music-tips">
        <section className="st-panel">
          <h2>Como usar</h2>
          <p>
            A API do Instagram não coloca música nos posts automáticos. Escolha a música aqui, copie o nome e adicione pelo app
            (Reels e Stories) ou use nos posts manuais. Contas comerciais têm uma biblioteca de músicas mais limitada: se a música
            não aparecer no app, ela não está liberada para a sua conta.
          </p>
        </section>
        <section className="st-panel">
          <h2>
            <Icon name="tiktok" size={18} /> Em alta no TikTok
          </h2>
          <p>A lista oficial do TikTok (TikTok One, Brasil) pede login com uma conta TikTok for Business.</p>
          <a className="st-btn st-btn-small" href={TIKTOK_TRENDS} target="_blank" rel="noreferrer">
            Abrir músicas em alta do TikTok
          </a>
        </section>
      </div>

      {chart?.stale && <p className="st-warn">A Apple não respondeu agora; mostrando a última lista que deu certo.</p>}
      {error && <p className="st-error">{error}</p>}

      <div className="st-filters">
        <input className="st-input" placeholder="Buscar música ou artista" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      {genres.length > 1 && (
        <div className="st-tabs">
          <button type="button" className={genre === "" ? "active" : ""} onClick={() => setGenre("")}>
            Todos
          </button>
          {genres.slice(0, 10).map((g) => (
            <button key={g} type="button" className={genre === g ? "active" : ""} onClick={() => setGenre(g)}>
              {g}
            </button>
          ))}
        </div>
      )}

      {loading && !chart ? (
        <div className="st-empty">Buscando as músicas em alta...</div>
      ) : list.length === 0 ? (
        <div className="st-empty">Nenhuma música encontrada.</div>
      ) : (
        <ol className="st-music-list">
          {list.map((s) => (
            <li key={s.id} className="st-music-row">
              <span className="st-music-rank">{s.rank}</span>
              <button
                type="button"
                className={playing === s.id ? "st-music-cover playing" : "st-music-cover"}
                onClick={() => toggle(s)}
                disabled={!s.previewUrl}
                title={s.previewUrl ? (playing === s.id ? "Parar prévia" : "Ouvir prévia de 30 s") : "Sem prévia"}
              >
                <img src={s.artwork} alt="" loading="lazy" />
                {s.previewUrl && <span className="st-music-play">{playing === s.id ? "❚❚" : "▶"}</span>}
              </button>
              <span className="st-music-info">
                <b>{s.name}</b>
                <span>
                  {s.artist}
                  {s.genres.length ? ` · ${s.genres.join(", ")}` : ""}
                  {s.durationMs ? ` · ${duration(s.durationMs)}` : ""}
                </span>
              </span>
              <span className="st-music-actions">
                <button type="button" className="st-btn st-btn-small" onClick={() => copy(s)}>
                  {copied === s.id ? "Copiado" : "Copiar"}
                </button>
                <a className="st-btn st-btn-small" href={s.url} target="_blank" rel="noreferrer" title="Abrir no Apple Music">
                  Apple Music
                </a>
              </span>
            </li>
          ))}
        </ol>
      )}
      {chart && <p className="st-hint">Lista e prévias: Apple. {chart.copyright}</p>}
    </div>
  );
}
