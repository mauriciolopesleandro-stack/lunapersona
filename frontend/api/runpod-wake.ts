import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated } from "./_auth.js";
import { NoGpuAvailableError, RunpodConfigError, wakeStudio } from "./_runpod.js";

// POST /api/runpod-wake
// Liga o estudio: usa o pod que ja estiver rodando, senao religa um parado,
// senao cria um novo com a GPU mais barata livre em um dos volumes (ver
// wakeStudio em _runpod.ts). Nao espera o backend ficar pronto (funcoes
// serverless tem timeout curto) - o frontend e quem faz o polling em
// /api/runpod-status ate aparecer backendReady.
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  if (req.method !== "POST") {
    res.statusCode = 405;
    res.end(JSON.stringify({ error: "Use POST." }));
    return;
  }
  if (!isAuthenticated(req)) {
    res.statusCode = 401;
    res.end(JSON.stringify({ error: "Sessao expirada. Entre de novo." }));
    return;
  }

  try {
    const result = await wakeStudio();
    res.statusCode = 200;
    res.end(
      JSON.stringify({
        alreadyRunning: result.alreadyRunning,
        action: result.action,
        podId: result.pod.id,
        dataCenterId: result.pod.dataCenterId,
        desiredStatus: result.pod.desiredStatus,
        attempts: result.attempts,
      })
    );
  } catch (err) {
    res.statusCode = err instanceof RunpodConfigError ? 501 : err instanceof NoGpuAvailableError ? 503 : 502;
    res.end(JSON.stringify({ error: err instanceof Error ? err.message : String(err) }));
  }
}
