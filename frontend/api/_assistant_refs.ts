import { AiError, askAi, visionModel, type AiContent } from "./_assistant.js";
import { igConnection } from "./_instagram.js";

// Referencias da Conversa: o administrador manda uma foto, um video (o
// navegador manda alguns quadros) ou um link (perfil do Instagram, post ou
// qualquer pagina). A IA com visao faz a leitura da IDEIA (cenario, roupa,
// pose, angulo, luz, clima); a Conversa usa essa leitura para recriar com a
// Luna. Nunca identifica nem copia a pessoa da referencia.

const MAX_IMAGES = 6;
const MAX_IMAGE_CHARS = 1_500_000; // ~1,1 MB por imagem em base64
const JPEG = /^data:image\/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+$/;

const READ_RULES = `Não identifique a pessoa nem descreva rosto, corpo ou traços físicos dela: a ideia vai ser recriada com outra personagem (a Luna). Não leia nem repita nomes de marcas. Responda em português do Brasil, em tópicos curtos, sem introdução.`;

const PHOTO_PROMPT = `Você ajuda a recriar ideias de conteúdo para redes sociais. Faça a leitura desta referência para recriar a IDEIA:
- Lugar e cenário
- Roupa (peças, cores, tecidos) e acessórios
- Pose e o que a pessoa está fazendo
- Enquadramento e ângulo da câmera (selfie, espelho, corpo inteiro, close…)
- Luz, hora do dia, cores e clima
- Estilo da foto (amadora, profissional, flash, celular…) e texto que aparece na imagem
${READ_RULES}`;

const VIDEO_PROMPT = `Você ajuda a recriar ideias de conteúdo para redes sociais. Estes são quadros de um vídeo, em ordem. Faça a leitura para recriar a IDEIA:
- Lugar, roupa e clima
- O que acontece do começo ao fim (movimento, gesto, dança, transição)
- Enquadramento, movimento da câmera e ritmo
- Texto na tela, se houver
${READ_RULES}`;

const PAGE_PROMPT = `Você ajuda a criar conteúdo para a Luna (influenciadora virtual brasileira) a partir de uma página de referência. Com as fotos mais curtidas e os dados abaixo, faça a leitura da página:
- Estilo visual que se repete (cenários, roupas, cores, luz, poses, enquadramento)
- Temas e formatos (foto, carrossel, Reels) que mais engajam, com base nas curtidas
- Tom das legendas (tamanho, emojis, chamadas)
- O que a Luna pode aproveitar e o que evitar
${READ_RULES}`;

export class RefError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
  }
}

async function read(prompt: string, images: string[], extra = ""): Promise<string> {
  const content: AiContent = [{ type: "text", text: extra ? `${prompt}\n\n${extra}` : prompt }, ...images.map((url) => ({ type: "image_url" as const, image_url: { url } }))];
  try {
    const out = await askAi("normal", [{ role: "user", content }], { model: visionModel(), maxTokens: 900, timeoutMs: 50_000 });
    return out.text.slice(0, 5000);
  } catch (e) {
    if (e instanceof AiError) throw new RefError(e.status, e.message);
    throw e;
  }
}

// Foto (1 imagem) ou video (quadros em ordem), ja reduzidos no navegador.
export async function analyzeImages(rawImages: unknown, kind: unknown): Promise<string> {
  const images = Array.isArray(rawImages) ? rawImages.map(String) : [];
  if (!images.length || images.length > MAX_IMAGES) throw new RefError(400, `Mande de 1 a ${MAX_IMAGES} imagens.`);
  if (images.some((i) => !JPEG.test(i) || i.length > MAX_IMAGE_CHARS)) throw new RefError(400, "Imagem inválida ou grande demais.");
  return read(kind === "video" ? VIDEO_PROMPT : PHOTO_PROMPT, images);
}

// --- links -----------------------------------------------------------------------------------------

// imagem da internet -> data URL (a IA nao precisa buscar o link)
async function asDataUrl(url: string): Promise<string | null> {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(10_000) });
    const type = r.headers.get("content-type") ?? "";
    if (!r.ok || !/^image\/(jpeg|png|webp)/.test(type)) return null;
    const buf = Buffer.from(await r.arrayBuffer());
    if (buf.length > 1_000_000) return null;
    return `data:${type.split(";")[0]};base64,${buf.toString("base64")}`;
  } catch {
    return null;
  }
}

interface Discovery {
  business_discovery?: {
    username: string;
    name?: string;
    biography?: string;
    followers_count?: number;
    media_count?: number;
    media?: {
      data: { caption?: string; media_type: string; media_url?: string; thumbnail_url?: string; like_count?: number; comments_count?: number; timestamp?: string }[];
    };
  };
  error?: { message?: string; code?: number };
}

// Perfil do Instagram: Business Discovery (conta profissional publica), pela
// conexao do Instagram do estudio via Facebook.
async function instagramProfile(username: string): Promise<{ title: string; analysis: string }> {
  const conn = await igConnection().catch(() => null);
  if (!conn) throw new RefError(400, "Conecte o Instagram (Configurações → Contas conectadas) para eu ler perfis.");
  if (conn.provider !== "facebook_login") {
    throw new RefError(400, "Para ler outros perfis, o Instagram precisa estar conectado pelo Facebook (página Instagram → Conectar pelo Facebook). Enquanto isso, mande prints ou as fotos.");
  }
  const fields = `business_discovery.username(${username}){username,name,biography,followers_count,media_count,media.limit(18){caption,media_type,media_url,thumbnail_url,like_count,comments_count,timestamp}}`;
  const r = await fetch(`${conn.host}/${conn.userId}?fields=${encodeURIComponent(fields)}`, { headers: { Authorization: `Bearer ${conn.token}` }, signal: AbortSignal.timeout(20_000) });
  const data = (await r.json().catch(() => ({}))) as Discovery;
  if (!r.ok || data.error || !data.business_discovery) {
    const msg = data.error?.message ?? `HTTP ${r.status}`;
    throw new RefError(
      400,
      `Não consegui ler @${username} pelo Instagram (${msg.slice(0, 160)}). Só dá para ler contas profissionais (criador ou empresa) e públicas. Se for o caso, mande prints ou as fotos.`
    );
  }
  const p = data.business_discovery;
  const posts = (p.media?.data ?? []).filter((m) => m.media_url || m.thumbnail_url);
  const top = [...posts].sort((a, b) => (b.like_count ?? 0) - (a.like_count ?? 0)).slice(0, MAX_IMAGES);
  const images = (await Promise.all(top.map((m) => asDataUrl((m.media_type === "VIDEO" ? m.thumbnail_url : m.media_url) ?? "")))).filter((x): x is string => Boolean(x));
  const kinds = (t: string) => (t === "VIDEO" ? "Reels/vídeo" : t === "CAROUSEL_ALBUM" ? "carrossel" : "foto");
  const facts = [
    `Perfil: @${p.username}${p.name ? ` (${p.name})` : ""} · ${p.followers_count ?? "?"} seguidores · ${p.media_count ?? "?"} posts`,
    p.biography ? `Bio: ${p.biography.slice(0, 300)}` : "",
    `Posts recentes (formato · curtidas · comentários · legenda):`,
    ...posts.slice(0, 18).map((m) => `- ${kinds(m.media_type)} · ${m.like_count ?? "?"} · ${m.comments_count ?? "?"} · ${(m.caption ?? "").replace(/\s+/g, " ").slice(0, 140)}`),
    images.length ? `As imagens anexas são os ${images.length} posts mais curtidos, do mais curtido para o menos.` : "Não consegui baixar as imagens: use só os dados acima.",
  ]
    .filter(Boolean)
    .join("\n");
  const analysis = images.length ? await read(PAGE_PROMPT, images, facts) : await read(PAGE_PROMPT, [], facts);
  return { title: `perfil @${p.username} do Instagram`, analysis: `${facts.split("\n").slice(0, 2).join("\n")}\n${analysis}` };
}

function meta(html: string, name: string): string | null {
  const re = new RegExp(`<meta[^>]+(?:property|name)=["']${name}["'][^>]*>`, "i");
  const tag = html.match(re)?.[0];
  const value = tag?.match(/content=["']([^"']*)["']/i)?.[1];
  return value ? value.replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;/g, "'").trim() : null;
}

// Qualquer pagina (inclusive post do Instagram): titulo, descricao e a imagem de previa.
async function pagePreview(url: string): Promise<{ title: string; analysis: string }> {
  let html = "";
  try {
    const r = await fetch(url, {
      headers: { "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)", Accept: "text/html" },
      redirect: "follow",
      signal: AbortSignal.timeout(12_000),
    });
    html = (await r.text()).slice(0, 400_000);
  } catch {
    throw new RefError(400, "Não consegui abrir o link. Mande um print ou a foto.");
  }
  const title = meta(html, "og:title") ?? html.match(/<title[^>]*>([^<]*)<\/title>/i)?.[1]?.trim() ?? url;
  const description = meta(html, "og:description") ?? meta(html, "description") ?? "";
  const imageUrl = meta(html, "og:image") ?? meta(html, "twitter:image");
  const image = imageUrl ? await asDataUrl(new URL(imageUrl, url).toString()) : null;
  if (!image && !description) throw new RefError(400, "A página não deixou eu ver a imagem nem o texto (o Instagram costuma bloquear). Mande um print ou a foto.");
  const facts = `Link: ${url}\nTítulo: ${title.slice(0, 200)}\nTexto: ${description.slice(0, 600)}`;
  const analysis = await read(PHOTO_PROMPT, image ? [image] : [], facts);
  return { title: title.slice(0, 80), analysis: `${facts}\n${analysis}` };
}

export async function analyzeLink(raw: unknown): Promise<{ title: string; analysis: string }> {
  let url: URL;
  try {
    url = new URL(String(raw ?? "").trim());
  } catch {
    throw new RefError(400, "Link inválido.");
  }
  if (!/^https?:$/.test(url.protocol)) throw new RefError(400, "Link inválido.");
  // so enderecos publicos (nada de rede interna)
  const h = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (h === "localhost" || h.endsWith(".local") || h.endsWith(".internal") || /^(127\.|10\.|0\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(h) || h.includes(":")) {
    throw new RefError(400, "Link inválido.");
  }
  const host = url.hostname.replace(/^www\./, "");
  if (host === "instagram.com" || host.endsWith(".instagram.com")) {
    const first = url.pathname.split("/").filter(Boolean)[0] ?? "";
    // instagram.com/<usuario>/ e um perfil; /p/, /reel/, /tv/, /stories/ sao posts
    if (first && !["p", "reel", "reels", "tv", "stories", "explore"].includes(first.toLowerCase()) && /^[A-Za-z0-9._]{1,30}$/.test(first)) {
      return instagramProfile(first);
    }
  }
  return pagePreview(url.toString());
}
