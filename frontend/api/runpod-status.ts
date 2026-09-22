import type { IncomingMessage, ServerResponse } from "http";
import { getPodAndBalance, RunpodConfigError } from "./_runpod";

// GET /api/runpod-status
// Retorna saldo da conta RunPod + estado/custo do pod, para o painel do frontend.
// So chama a API da RunPod (nao acorda nem consulta o ComfyUI) - custo zero de GPU.
export default async function handler(_req: IncomingMessage, res: ServerResponse) {
  try {
    const data = await getPodAndBalance();
    const pod = data.pod;
    const running = pod?.desiredStatus === "RUNNING";
    const costPerHr = pod ? Number(pod.costPerHr) : 0;
    const uptimeSeconds = pod?.runtime?.uptimeInSeconds ?? 0;
    const liveSpend = running ? (uptimeSeconds / 3600) * costPerHr : 0;

    res.setHeader("Content-Type", "application/json");
    res.statusCode = 200;
    res.end(
      JSON.stringify({
        running,
        desiredStatus: pod?.desiredStatus ?? "UNKNOWN",
        costPerHr,
        uptimeSeconds,
        liveSpend,
        balance: data.myself.clientBalance,
      })
    );
  } catch (err) {
    res.setHeader("Content-Type", "application/json");
    res.statusCode = err instanceof RunpodConfigError ? 501 : 502;
    res.end(JSON.stringify({ error: err instanceof Error ? err.message : String(err) }));
  }
}
