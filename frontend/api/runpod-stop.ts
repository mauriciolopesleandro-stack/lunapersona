import type { IncomingMessage, ServerResponse } from "http";
import { getPodAndBalance, stopPod, RunpodConfigError } from "./_runpod.js";

// POST /api/runpod-stop
// Desliga o pod manualmente (botao "Desligar" no topo do site). Mesma ideia
// do runpod-wake: roda na Vercel, entao funciona mesmo se o backend no pod
// estiver travado.
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  if (req.method !== "POST") {
    res.statusCode = 405;
    res.end(JSON.stringify({ error: "Use POST." }));
    return;
  }

  try {
    const current = await getPodAndBalance();
    if (current.pod?.desiredStatus !== "RUNNING") {
      res.setHeader("Content-Type", "application/json");
      res.statusCode = 200;
      res.end(JSON.stringify({ alreadyStopped: true }));
      return;
    }

    const result = await stopPod();
    res.setHeader("Content-Type", "application/json");
    res.statusCode = 200;
    res.end(JSON.stringify({ alreadyStopped: false, desiredStatus: result.podStop.desiredStatus }));
  } catch (err) {
    res.setHeader("Content-Type", "application/json");
    res.statusCode = err instanceof RunpodConfigError ? 501 : 502;
    res.end(JSON.stringify({ error: err instanceof Error ? err.message : String(err) }));
  }
}
