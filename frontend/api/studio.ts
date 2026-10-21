import type { IncomingMessage, ServerResponse } from "http";
import jobs from "./_jobs.js";
import library from "./_library.js";
import posts from "./_posts_api.js";

// /api/studio?r=<rota> - uma funcao so para as rotas do estudio novo. O plano
// Hobby da Vercel aceita no maximo 12 funcoes por deploy (projeto sem
// framework: cada arquivo de api/ conta uma); as rotas ficam em arquivos com
// "_" (que nao viram funcao) e esta escolhe pela rota.
//   r=library  biblioteca, personagens, campanhas, auditoria (Fase 1)
//   r=posts    publicacoes com aprovacao (Fase 2)
//   r=jobs     processador da fila (Supabase Cron ou botao do painel)
const ROUTES: Record<string, (req: IncomingMessage, res: ServerResponse) => Promise<unknown>> = { library, posts, jobs };

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  const route = new URL(req.url ?? "/", "http://local").searchParams.get("r") ?? "";
  const run = ROUTES[route];
  if (!run) {
    res.statusCode = 404;
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ detail: "Rota desconhecida." }));
    return;
  }
  await run(req, res);
}
