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

const READ_RULES = `Descreva só o que aparece de verdade na imagem; não invente pessoa, roupa ou cenário que não estão lá (se não houver pessoa, diga). Não identifique a pessoa nem descreva rosto, corpo ou traços físicos dela: a ideia vai ser recriada com outra personagem (a Luna). Não leia nem repita nomes de marcas. Responda em português do Brasil, em tópicos curtos, sem introdução.`;

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

// Revisao: o administrador manda uma foto ou um Reels DA LUNA (gerado pela
// Naiaflow) e pede para achar o que esta errado. A IA olha com cuidado (pouca
// criatividade) e a Conversa transforma isso em prompt ou pedido de correcao.
const REVIEW_PHOTO = `Você revisa fotos da Luna (influenciadora virtual brasileira, gerada por IA na Naiaflow) antes de postar no Instagram. Olhe a foto com atenção e responda em tópicos curtos, em português do Brasil:
- O que aparece: roupa de verdade (parte de cima e de baixo; diga "sem nada em cima" se for o caso), lugar, pose, enquadramento.
- Defeitos de IA: mãos e dedos (quantos, tortos, fundidos), braços ou pernas a mais/a menos ou em ângulo impossível, rosto deformado ou diferente em partes, olhos, dentes, roupa que se mistura com a pele, objetos derretidos, texto estranho, emendas, manchas ou borrões.
- Enquadramento: cabeça, mãos ou pés cortados; foto torta; elemento cortando o rosto.
- Regras do Instagram: nudez (seio, mamilo, partes íntimas à mostra) ou algo que o Instagram derruba.
- Estilo: parece foto de celular natural de influenciadora ou artificial/produzida demais (pele de plástico, luz de estúdio)?
- Nota de 0 a 10 para postar e, se tiver problema, o que corrigir (bem objetivo).
Descreva só o que aparece de verdade; se estiver tudo certo, diga que está pronta para postar.`;

const REVIEW_VIDEO = `Você revisa um Reels da Luna (influenciadora virtual brasileira, gerada por IA). Estes são quadros do vídeo, em ordem. Responda em tópicos curtos, em português do Brasil:
- O que acontece no vídeo e a roupa/lugar.
- Defeitos de IA entre os quadros: rosto que muda de um quadro para outro, mãos e dedos deformados, braços/pernas que somem ou se fundem, roupa que muda ou some, fundo que tremula ou derrete, objetos que aparecem e somem.
- Enquadramento: cabeça ou pés saindo do quadro, movimento cortado.
- Regras do Instagram: nudez ou algo que o Instagram derruba.
- Nota de 0 a 10 para postar e o que corrigir (bem objetivo).
Descreva só o que aparece de verdade nos quadros.`;

export class RefError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
  }
}

async function read(prompt: string, images: string[], extra = "", temperature?: number): Promise<string> {
  const content: AiContent = [{ type: "text", text: extra ? `${prompt}\n\n${extra}` : prompt }, ...images.map((url) => ({ type: "image_url" as const, image_url: { url } }))];
  try {
    const out = await askAi("normal", [{ role: "user", content }], { model: visionModel(), maxTokens: 900, timeoutMs: 40_000, temperature });
    return out.text.slice(0, 5000);
  } catch (e) {
    // lote de fotos recusado pelo provedor: tenta de novo com menos fotos
    if (e instanceof AiError && e.status === 502 && images.length > 1) return read(prompt, images.slice(0, images.length > 3 ? 3 : 1), extra, temperature);
    if (e instanceof AiError) throw new RefError(e.status, e.message);
    throw e;
  }
}

// Foto (1 imagem) ou video (quadros em ordem), ja reduzidos no navegador.
// purpose "review": revisao de foto/Reels da Luna (o que esta errado) em vez da
// leitura da ideia de uma referencia
export async function analyzeImages(rawImages: unknown, kind: unknown, purpose?: unknown): Promise<string> {
  const images = Array.isArray(rawImages) ? rawImages.map(String) : [];
  if (!images.length || images.length > MAX_IMAGES) throw new RefError(400, `Mande de 1 a ${MAX_IMAGES} imagens.`);
  if (images.some((i) => !JPEG.test(i) || i.length > MAX_IMAGE_CHARS)) throw new RefError(400, "Imagem inválida ou grande demais.");
  if (purpose === "review") return read(kind === "video" ? REVIEW_VIDEO : REVIEW_PHOTO, images, "", 0.1);
  return read(kind === "video" ? VIDEO_PROMPT : PHOTO_PROMPT, images);
}

// --- links -----------------------------------------------------------------------------------------

// imagem da internet -> data URL (a IA nao precisa buscar o link)
async function asDataUrl(url: string): Promise<string | null> {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(10_000) });
    const type = r.headers.get("content-type") ?? "";
    // so JPEG e PNG (nem todo provedor da IA aceita webp)
    if (!r.ok || !/^image\/(jpeg|png)/.test(type)) return null;
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
