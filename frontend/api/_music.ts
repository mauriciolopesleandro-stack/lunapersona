import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated } from "./_auth.js";

// /api/studio?r=music - musicas em alta no Brasil, de fontes oficiais e
// gratuitas da Apple (sem chave):
//   - feed "Top musicas" do Apple Music Brasil (rss.marketingtools.apple.com)
//   - prévia de 30 s pela API de busca do iTunes (itunes.apple.com/lookup)
// Instagram e TikTok nao tem API publica de musicas em alta (a lista do TikTok
// One exige login), entao entram so como atalho na tela.
// Cache de 1 hora por instancia da funcao: o feed muda poucas vezes por dia.

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

const FEED = "https://rss.marketingtools.apple.com/api/v2/br/music/most-played/50/songs.json";
const LOOKUP = "https://itunes.apple.com/lookup";
const TTL_MS = 60 * 60 * 1000;
let cache: { at: number; body: object } | null = null;

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { signal: AbortSignal.timeout(15_000) });
  if (!res.ok) throw new Error(`HTTP ${res.status} em ${new URL(url).host}`);
  return (await res.json()) as T;
}

async function load(): Promise<object> {
  const feed = await getJson<{
    feed: {
      updated: string;
      copyright: string;
      results: { id: string; name: string; artistName: string; artworkUrl100: string; url: string; releaseDate?: string; genres?: { name: string }[] }[];
    };
  }>(FEED);
  const results = feed.feed.results ?? [];
  // previas em uma chamada so (a API aceita varios ids separados por virgula)
  const previews = new Map<string, { previewUrl?: string; trackTimeMillis?: number }>();
  if (results.length) {
    try {
      const lookup = await getJson<{ results: { trackId: number; previewUrl?: string; trackTimeMillis?: number }[] }>(
        `${LOOKUP}?id=${results.map((r) => r.id).join(",")}&country=br`
      );
      for (const r of lookup.results ?? []) previews.set(String(r.trackId), r);
    } catch {
      // sem previa nao impede a lista
    }
  }
  const songs: Song[] = results.map((r, i) => ({
    rank: i + 1,
    id: r.id,
    name: r.name,
    artist: r.artistName,
    genres: (r.genres ?? []).map((g) => g.name).filter((g) => g !== "Música" && g !== "Music"),
    // capa maior que a de 100 px do feed
    artwork: r.artworkUrl100.replace("100x100bb", "200x200bb"),
    url: r.url,
    releaseDate: r.releaseDate ?? null,
    previewUrl: previews.get(r.id)?.previewUrl ?? null,
    durationMs: previews.get(r.id)?.trackTimeMillis ?? null,
  }));
  return { source: "Apple Music · Top músicas Brasil", updated: feed.feed.updated, copyright: feed.feed.copyright, songs };
}

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (!(await isAuthenticated(req))) return reply(401, { detail: "Sessão expirada. Entre de novo." });
  if (req.method !== "GET") return reply(405, { detail: "Use GET." });
  const refresh = new URL(req.url ?? "/", "http://local").searchParams.get("refresh") === "1";
  try {
    if (!cache || refresh || Date.now() - cache.at > TTL_MS) cache = { at: Date.now(), body: await load() };
    return reply(200, { ...cache.body, cachedAt: new Date(cache.at).toISOString() });
  } catch (e) {
    // se a Apple falhar, devolve a ultima lista boa (avisando)
    if (cache) return reply(200, { ...cache.body, cachedAt: new Date(cache.at).toISOString(), stale: true });
    return reply(502, { detail: `Não consegui buscar as músicas em alta: ${e instanceof Error ? e.message : e}` });
  }
}
