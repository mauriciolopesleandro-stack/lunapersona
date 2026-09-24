import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated } from "./_auth.js";
import {
  enforceSingleRunningPod,
  getBalance,
  pickCurrentPod,
  podApiBase,
  RunpodConfigError,
  STUDIO_POD_PREFIX,
} from "./_runpod.js";

const HEALTH_TIMEOUT_MS = 4_000;

// O pod pode estar "RUNNING" bem antes do backend responder (imagem
// baixando, autostart sincronizando o volume e subindo Ollama/backend).
async function backendIsReady(apiBase: string): Promise<boolean> {
  try {
    const res = await fetch(`${apiBase}/health`, { signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS) });
    return res.ok;
  } catch {
    return false;
  }
}

// GET /api/runpod-status
// Saldo da conta + estado/custo do pod atual do estudio e o endereco do
// backend dele (apiBase) - o pod muda quando o estudio sobe em outra
// maquina/volume, entao o frontend sempre pega o endereco daqui.
// So consulta a API da RunPod e o /health do backend - custo zero de GPU.
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  if (!isAuthenticated(req)) {
    res.statusCode = 401;
    res.end(JSON.stringify({ error: "Sessao expirada. Entre de novo." }));
    return;
  }

  try {
    // De quebra aplica a regra de nunca ter mais de um pod ligado.
    const [{ pods, stopped }, balance] = await Promise.all([enforceSingleRunningPod(), getBalance()]);
    const pod = pickCurrentPod(pods.filter((p) => p.name.startsWith(STUDIO_POD_PREFIX)));
    const running = pod?.desiredStatus === "RUNNING";
    const costPerHr = pod?.costPerHr ?? 0;
    const uptimeSeconds =
      running && pod?.lastStartedAt ? Math.max(0, (Date.now() - Date.parse(pod.lastStartedAt)) / 1000) : 0;
    const apiBase = pod ? podApiBase(pod.id) : null;

    res.statusCode = 200;
    res.end(
      JSON.stringify({
        running,
        backendReady: running && apiBase ? await backendIsReady(apiBase) : false,
        desiredStatus: pod?.desiredStatus ?? "UNKNOWN",
        podId: pod?.id ?? null,
        dataCenterId: pod?.dataCenterId ?? null,
        gpu: pod?.gpuDisplayName ?? null,
        apiBase,
        costPerHr,
        uptimeSeconds,
        liveSpend: running ? (uptimeSeconds / 3600) * costPerHr : 0,
        balance,
        volumeSync: Boolean(process.env.RUNPOD_S3_ACCESS_KEY && process.env.RUNPOD_S3_SECRET_KEY),
        stoppedExtraPods: stopped,
      })
    );
  } catch (err) {
    res.statusCode = err instanceof RunpodConfigError ? 501 : 502;
    res.end(JSON.stringify({ error: err instanceof Error ? err.message : String(err) }));
  }
}
