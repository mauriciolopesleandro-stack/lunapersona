import type { IncomingMessage, ServerResponse } from "http";
import instagram from "./_instagram_api.js";
import jobs from "./_jobs.js";
import library from "./_library.js";
import music from "./_music.js";
import posts from "./_posts_api.js";
import tiktok, { tiktokCallback } from "./_tiktok_api.js";

// /api/studio?r=<rota> - uma funcao so para as rotas do estudio novo. O plano
// Hobby da Vercel aceita no maximo 12 funcoes por deploy (projeto sem
// framework: cada arquivo de api/ conta uma); as rotas ficam em arquivos com
// "_" (que nao viram funcao) e esta escolhe pela rota.
//   r=library  biblioteca, personagens, campanhas, auditoria (Fase 1)
//   r=posts    publicacoes com aprovacao (Fase 2)
//   r=jobs     processador da fila (Supabase Cron ou botao do painel)
//   r=instagram conexao da conta do Instagram (publicacao automatica)
//   r=music    musicas em alta no Brasil (Apple Music, fontes oficiais)
//   r=tiktok   conexao do TikTok (rascunho automatico); a volta do OAuth chega
//              em /api/tiktok/callback, reescrito pelo vercel.json
const ROUTES: Record<string, (req: IncomingMessage, res: ServerResponse) => Promise<unknown>> = { library, posts, jobs, instagram, music, tiktok, "tiktok-callback": tiktokCallback };

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  const url = new URL(req.url ?? "/", "http://local");
  // a volta do TikTok chega pelo caminho (o TikTok nao aceita query na redirect_uri)
  const route = url.searchParams.get("r") ?? (url.pathname.endsWith("/tiktok/callback") ? "tiktok-callback" : "");
  const run = ROUTES[route];
  if (!run) {
    res.statusCode = 404;
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ detail: "Rota desconhecida." }));
    return;
  }
  await run(req, res);
}
