import type { IncomingMessage, ServerResponse } from "http";
import assistant from "./_assistant.js";
import instagram, { instagramCallback } from "./_instagram_api.js";
import jobs from "./_jobs.js";
import library from "./_library.js";
import music from "./_music.js";
import posts from "./_posts_api.js";
import tiktok, { tiktokCallback } from "./_tiktok_api.js";
import tiktokMedia from "./_tiktok_media.js";
import telegramHook from "./_tg_review.js";

// /api/studio?r=<rota> - uma funcao so para as rotas do estudio novo. O plano
// Hobby da Vercel aceita no maximo 12 funcoes por deploy (projeto sem
// framework: cada arquivo de api/ conta uma); as rotas ficam em arquivos com
// "_" (que nao viram funcao) e esta escolhe pela rota.
//   r=library  biblioteca, personagens, campanhas, auditoria (Fase 1)
//   r=posts    publicacoes com aprovacao (Fase 2)
//   r=jobs     processador da fila (Supabase Cron ou botao do painel)
//   r=instagram conexao da conta do Instagram (publicacao automatica); a volta do
//              Login do Facebook chega em /api/instagram/callback (vercel.json)
//   r=music    musicas em alta no Brasil (Apple Music, fontes oficiais)
//   r=tiktok   conexao do TikTok (rascunho automatico); a volta do OAuth chega
//              em /api/tiktok/callback, reescrito pelo vercel.json
//   r=telegram-hook botoes de aprovacao do Telegram (webhook em
//              /api/telegram/webhook, reescrito pelo vercel.json)
//   r=assistant chat do administrador com a IA (OpenRouter) que conhece a Luna
//   r=tiktok-media foto temporaria para o TikTok baixar (link assinado em
//              /api/tiktok/media/....jpg, reescrito pelo vercel.json)
const ROUTES: Record<string, (req: IncomingMessage, res: ServerResponse) => Promise<unknown>> = { library, posts, jobs, instagram, music, tiktok, "tiktok-callback": tiktokCallback, "tiktok-media": tiktokMedia, "instagram-callback": instagramCallback, assistant, "telegram-hook": telegramHook };

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  const url = new URL(req.url ?? "/", "http://local");
  // a volta do TikTok chega pelo caminho (o TikTok nao aceita query na redirect_uri)
  const route =
    url.searchParams.get("r") ??
    (url.pathname.endsWith("/tiktok/callback")
      ? "tiktok-callback"
      : url.pathname.endsWith("/instagram/callback")
      ? "instagram-callback"
      : url.pathname.endsWith("/telegram/webhook")
      ? "telegram-hook"
      : url.pathname.startsWith("/api/tiktok/media/")
      ? "tiktok-media"
      : "");
  const run = ROUTES[route];
  if (!run) {
    res.statusCode = 404;
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ detail: "Rota desconhecida." }));
    return;
  }
  await run(req, res);
}
