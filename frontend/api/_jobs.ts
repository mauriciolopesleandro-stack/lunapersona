import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated, safeEqual } from "./_auth.js";
import { NETWORK_RULES, type PostRow } from "./_posts.js";
import { dbInsert, dbRpc, dbSelect, dbUpdate, dbUpsert, supabaseConfigured } from "./_supabase.js";
import { sendNotice, telegramConfig } from "./_telegram.js";

// /api/studio?r=jobs - processa a fila (scheduled_jobs). Chamado:
//  - a cada minuto pelo Supabase Cron (POST com "Authorization: Bearer <JOBS_SECRET>"),
//    para funcionar com o computador desligado;
//  - ou pelo botao "Processar agora" do painel (com a sessao do administrador).
// Nada roda em segundo plano na Vercel: cada chamada pega as tarefas vencidas
// (com trava no banco), executa e termina.
//
// Tarefa "lembrete_publicacao": no horario, a publicacao aprovada passa para
// "publicando" e o administrador recebe o aviso no Telegram para publicar a
// mao e confirmar no estudio. Falha no aviso: tenta de novo em 2, 4, 8 min.

interface JobRow {
  id: string;
  kind: string;
  post_id: string;
  post_version: number;
  run_at: string;
  attempts: number;
  max_attempts: number;
}

const BATCH = 10;

function authorizedByCron(req: IncomingMessage): boolean {
  const secret = process.env.JOBS_SECRET;
  const header = req.headers.authorization ?? "";
  return Boolean(secret && secret.length >= 32 && header.startsWith("Bearer ") && safeEqual(header.slice(7), secret));
}

function appUrl(req: IncomingMessage): string {
  if (process.env.PUBLIC_APP_URL) return process.env.PUBLIC_APP_URL.replace(/\/+$/, "");
  const host = req.headers["x-forwarded-host"] ?? req.headers.host;
  return host ? `https://${host}` : "";
}

async function finish(job: JobRow, patch: Record<string, unknown>) {
  await dbUpdate("scheduled_jobs", `id=eq.${job.id}`, { ...patch, locked_at: null });
}

async function runReminder(job: JobRow, url: string): Promise<"ok" | "obsoleta" | "nova_tentativa" | "falhou"> {
  const [post] = await dbSelect<PostRow>("posts", `select=*&id=eq.${job.post_id}`);
  // Publicacao mudou, foi cancelada ou ja publicada: o lembrete perdeu o sentido.
  const current = post && post.version === job.post_version && (post.status === "agendado" || post.status === "publicando");
  if (!current) {
    await finish(job, { status: "cancelado", finished_at: new Date().toISOString(), last_error: "publicação mudou ou foi cancelada" });
    return "obsoleta";
  }
  if (post.status === "agendado") {
    // so passa se continuar agendada na mesma versao (outra execucao pode ter mexido)
    await dbUpdate("posts", `id=eq.${post.id}&status=eq.agendado&version=eq.${post.version}`, { status: "publicando" });
  }
  const label = NETWORK_RULES[post.network].label;
  const dests = await dbSelect<{ format: string }>("post_destinations", `select=format&post_id=eq.${post.id}&status=eq.pendente`);
  const text = [
    `⏰ Hora de publicar no ${label} (${dests.map((d) => d.format).join(", ") || "sem destino"}).`,
    post.caption ? `Legenda: ${post.caption.slice(0, 300)}${post.caption.length > 300 ? "…" : ""}` : "Sem legenda.",
    `Depois de publicar, confirme no estúdio${url ? `: ${url}` : "."}`,
  ].join("\n\n");
  try {
    await sendNotice(await telegramConfig(), text);
    await finish(job, { status: "concluido", finished_at: new Date().toISOString(), last_error: null });
    await dbInsert("publishing_attempts", { post_id: post.id, job_id: job.id, attempt: job.attempts, channel: "lembrete_telegram", outcome: "ok", detail: "aviso enviado" });
    return "ok";
  } catch (e) {
    const error = (e instanceof Error ? e.message : String(e)).slice(0, 300);
    await dbInsert("publishing_attempts", { post_id: post.id, job_id: job.id, attempt: job.attempts, channel: "lembrete_telegram", outcome: "erro", detail: error });
    if (job.attempts >= job.max_attempts) {
      await finish(job, { status: "falhou", finished_at: new Date().toISOString(), last_error: error });
      return "falhou";
    }
    const retryAt = new Date(Date.now() + 2 ** job.attempts * 60_000).toISOString();
    await finish(job, { status: "pendente", run_at: retryAt, last_error: error });
    return "nova_tentativa";
  }
}

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (req.method !== "POST") return reply(405, { detail: "Use POST." });
  const byCron = authorizedByCron(req);
  if (!byCron && !(await isAuthenticated(req))) return reply(401, { detail: "Não autorizado." });
  if (!supabaseConfigured()) return reply(501, { configured: false, detail: "Banco (Supabase) ainda não configurado na Vercel." });

  const result = { origem: byCron ? "cron" : "painel", processadas: 0, ok: 0, obsoletas: 0, novas_tentativas: 0, falhas: 0, erro: null as string | null };
  try {
    const jobs = await dbRpc<JobRow[]>("claim_jobs", { p_limit: BATCH });
    const url = appUrl(req);
    for (const job of jobs) {
      result.processadas++;
      const outcome = job.kind === "lembrete_publicacao" ? await runReminder(job, url) : "obsoleta";
      if (outcome === "ok") result.ok++;
      else if (outcome === "obsoleta") result.obsoletas++;
      else if (outcome === "nova_tentativa") result.novas_tentativas++;
      else result.falhas++;
    }
  } catch (e) {
    result.erro = (e instanceof Error ? e.message : String(e)).slice(0, 300);
  }
  await dbUpsert("worker_status", { id: 1, last_run_at: new Date().toISOString(), last_result: result }, "id").catch(() => undefined);
  return reply(result.erro ? 502 : 200, result);
}
