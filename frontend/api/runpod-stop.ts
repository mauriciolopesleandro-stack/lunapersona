import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated } from "./_auth.js";
import { listStudioPods, RunpodConfigError, stopPod } from "./_runpod.js";

// POST /api/runpod-stop
// Desliga o pod manualmente (botao "Desligar" no topo do site). Mesma ideia
// do runpod-wake: roda na Vercel, entao funciona mesmo se o backend no pod
// estiver travado.
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
    const running = (await listStudioPods()).filter((p) => p.desiredStatus === "RUNNING");
    if (running.length === 0) {
      res.statusCode = 200;
      res.end(JSON.stringify({ alreadyStopped: true }));
      return;
    }

    await Promise.all(running.map((p) => stopPod(p.id)));
    res.statusCode = 200;
    res.end(JSON.stringify({ alreadyStopped: false, desiredStatus: "EXITED" }));
  } catch (err) {
    res.statusCode = err instanceof RunpodConfigError ? 501 : 502;
    res.end(JSON.stringify({ error: err instanceof Error ? err.message : String(err) }));
  }
}
