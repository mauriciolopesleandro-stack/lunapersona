import type { IncomingMessage, ServerResponse } from "http";
import { getPodAndBalance, resumePod, RunpodConfigError } from "./_runpod.js";

// POST /api/runpod-wake
// Dispara o religamento do pod se ele nao estiver rodando. Nao espera o
// ComfyUI ficar pronto (funcoes serverless tem timeout curto) - o frontend
// e quem faz o polling em /api/runpod-status ate o pod aparecer "running".
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  if (req.method !== "POST") {
    res.statusCode = 405;
    res.end(JSON.stringify({ error: "Use POST." }));
    return;
  }

  try {
    const current = await getPodAndBalance();
    if (current.pod?.desiredStatus === "RUNNING") {
      res.setHeader("Content-Type", "application/json");
      res.statusCode = 200;
      res.end(JSON.stringify({ alreadyRunning: true }));
      return;
    }

    const result = await resumePod();
    res.setHeader("Content-Type", "application/json");
    res.statusCode = 200;
    res.end(JSON.stringify({ alreadyRunning: false, desiredStatus: result.podResume.desiredStatus }));
  } catch (err) {
    res.setHeader("Content-Type", "application/json");
    res.statusCode = err instanceof RunpodConfigError ? 501 : 502;
    res.end(JSON.stringify({ error: err instanceof Error ? err.message : String(err) }));
  }
}
