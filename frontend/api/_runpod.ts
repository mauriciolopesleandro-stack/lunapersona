// Cliente da API da RunPod usado pelas funcoes serverless (wake/status/stop/
// bootstrap). So roda no servidor (Vercel Functions) - a RUNPOD_API_KEY
// nunca chega ao bundle do navegador.
//
// O estudio nao depende mais de UM pod fixo (RUNPOD_POD_ID): um pod parado
// fica preso a maquina fisica onde foi criado, e quando essa maquina lota
// ("not enough free GPUs on the host machine") ele nao liga mais, mesmo com
// GPU sobrando no resto do datacenter. Agora, para ligar:
//
//   1. se ja existe um pod do estudio rodando, usa ele;
//   2. tenta religar os pods do estudio parados (mais rapido: imagem ja
//      baixada na maquina);
//   3. cria um pod novo, com a GPU mais barata livre, em cada volume da
//      lista VOLUMES, na ordem (primeiro o US-MO-2, depois o EU-RO-1).
//
// Os dois volumes tem o mesmo conteudo: o proprio pod sincroniza um com o
// outro via API S3 da RunPod (scripts/volume_sync.py), entao tanto faz em
// qual deles o estudio sobe.
const RUNPOD_REST_URL = "https://rest.runpod.io/v1";
const RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql";

export class RunpodConfigError extends Error {}

// Mesmo texto que a RunPod devolve quando falta GPU - o frontend
// (LoginPage) reconhece essa frase e mostra a tela de "sem GPU" com retry.
export class NoGpuAvailableError extends Error {}

export interface StudioVolume {
  id: string;
  dataCenterId: string;
}

// Ordem = preferencia. O primeiro e o volume original (mais perto do
// Brasil); o segundo e a copia sincronizada.
export const VOLUMES: StudioVolume[] = [
  { id: "1o5y5cpw99", dataCenterId: "US-MO-2" }, // luna-models
  { id: "7s449owvmb", dataCenterId: "EU-RO-1" }, // luna-models-ro
];

// Precos por hora da Secure Cloud (a unica que aceita network volume),
// usados so para filtrar pelo teto e ordenar da mais barata para a mais cara.
// Todas com 24 GB+ de VRAM: Chroma1-HD fp8 + T5 (~15 GB) e o modelo do chat
// no Ollama precisam caber juntos.
const GPU_OPTIONS: Array<{ id: string; pricePerHr: number }> = [
  { id: "NVIDIA RTX A5000", pricePerHr: 0.27 },
  { id: "NVIDIA L4", pricePerHr: 0.49 },
  { id: "NVIDIA A40", pricePerHr: 0.49 },
  { id: "NVIDIA GeForce RTX 3090", pricePerHr: 0.5 },
  { id: "NVIDIA RTX A6000", pricePerHr: 0.53 },
  { id: "NVIDIA RTX PRO 4000 Blackwell", pricePerHr: 0.57 },
  { id: "NVIDIA GeForce RTX 4090", pricePerHr: 0.74 },
  { id: "NVIDIA L40", pricePerHr: 0.82 },
  { id: "NVIDIA RTX 6000 Ada Generation", pricePerHr: 0.84 },
];

const DEFAULT_MAX_PRICE_PER_HR = 0.6;

export const STUDIO_POD_PREFIX = "luna-studio";

// Mesma imagem que o estudio ja usava, na variante CUDA 12.8: o PyTorch vem
// da imagem (o venv do ComfyUI no volume usa --system-site-packages), entao
// as duas variantes enxergam o mesmo volume - e a 12.8 roda em muito mais
// maquinas que a 13.0.
const IMAGE_NAME = "runpod/comfyui:1.3.2-comfyuiv0.30.0-cuda12.8";

// Chave publica da automacao (nao e segredo). A privada correspondente fica
// em RUNPOD_SSH_PRIVATE_KEY na Vercel, usada por /api/runpod-bootstrap.
const SSH_PUBLIC_KEY =
  "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHRXQLRkSq6Tk9LbYedSD9tAz8+9In2bxL6tt+O72IXC luna-runpod-wake";

// O entrypoint da imagem e /start.sh (sobe SSH, Jupyter e ComfyUI). Antes
// dele, baixa e dispara em segundo plano o nosso autostart (sincroniza o
// volume e sobe Ollama + backend) - assim o pod fica pronto sozinho, sem
// depender do SSH pela Vercel. Se o GitHub falhar, o pod sobe igual.
const AUTOSTART_URL =
  "https://raw.githubusercontent.com/mauriciolopesleandro-stack/lunapersona/main/scripts/pod_autostart.sh";
const DOCKER_ENTRYPOINT = [
  "bash",
  "-c",
  `curl -fsSL ${AUTOSTART_URL} -o /tmp/luna-autostart.sh && (nohup bash /tmp/luna-autostart.sh > /tmp/luna-autostart.log 2>&1 &); exec /start.sh`,
];

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new RunpodConfigError(`Variavel de ambiente ${name} nao configurada na Vercel.`);
  }
  return value;
}

export function maxPricePerHr(): number {
  const raw = Number(process.env.LUNA_MAX_GPU_PRICE);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_MAX_PRICE_PER_HR;
}

async function rest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${RUNPOD_REST_URL}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${requireEnv("RUNPOD_API_KEY")}`,
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`RunPod API ${method} ${path} respondeu ${res.status}: ${text.slice(0, 400)}`);
  }
  return (text ? JSON.parse(text) : {}) as T;
}

export async function runpodGraphQL<T>(query: string, variables?: Record<string, unknown>): Promise<T> {
  const apiKey = requireEnv("RUNPOD_API_KEY");
  const res = await fetch(`${RUNPOD_GRAPHQL_URL}?api_key=${encodeURIComponent(apiKey)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });

  if (!res.ok) {
    throw new Error(`RunPod API respondeu ${res.status}: ${await res.text()}`);
  }
  const json = (await res.json()) as { data?: T; errors?: Array<{ message: string }> };
  if (json.errors?.length) {
    throw new Error(`RunPod API erro: ${json.errors.map((e) => e.message).join("; ")}`);
  }
  return json.data as T;
}

export interface StudioPod {
  id: string;
  name: string;
  desiredStatus: string; // RUNNING | EXITED | TERMINATED
  costPerHr: number;
  publicIp: string | null;
  portMappings: Record<string, number> | null;
  lastStartedAt: string | null;
  dataCenterId: string | null;
  networkVolumeId: string | null;
  gpuDisplayName: string | null;
}

interface RestPod {
  id: string;
  name?: string;
  desiredStatus?: string;
  costPerHr?: number | string;
  publicIp?: string | null;
  portMappings?: Record<string, number> | null;
  lastStartedAt?: string | null;
  machine?: { dataCenterId?: string; gpuDisplayName?: string } | null;
  networkVolume?: { id?: string; dataCenterId?: string } | null;
}

function toStudioPod(p: RestPod): StudioPod {
  return {
    id: p.id,
    name: p.name ?? "",
    desiredStatus: p.desiredStatus ?? "UNKNOWN",
    costPerHr: Number(p.costPerHr ?? 0),
    publicIp: p.publicIp ?? null,
    portMappings: p.portMappings ?? null,
    lastStartedAt: p.lastStartedAt ?? null,
    dataCenterId: p.networkVolume?.dataCenterId ?? p.machine?.dataCenterId ?? null,
    networkVolumeId: p.networkVolume?.id ?? null,
    gpuDisplayName: p.machine?.gpuDisplayName ?? null,
  };
}

function volumeRank(pod: StudioPod): number {
  const idx = VOLUMES.findIndex((v) => v.id === pod.networkVolumeId);
  return idx === -1 ? VOLUMES.length : idx;
}

export async function listStudioPods(): Promise<StudioPod[]> {
  const pods = await rest<RestPod[]>("GET", "/pods?includeMachine=true&includeNetworkVolume=true");
  return pods
    .map(toStudioPod)
    .filter((p) => p.name.startsWith(STUDIO_POD_PREFIX) && p.desiredStatus !== "TERMINATED");
}

// O pod que o frontend deve usar: o que estiver rodando; senao, o mais
// recente (para mostrar status/custo mesmo desligado).
export function pickCurrentPod(pods: StudioPod[]): StudioPod | null {
  const running = pods.find((p) => p.desiredStatus === "RUNNING");
  if (running) return running;
  const sorted = [...pods].sort((a, b) => (b.lastStartedAt ?? "").localeCompare(a.lastStartedAt ?? ""));
  return sorted[0] ?? null;
}

export async function getCurrentPod(): Promise<StudioPod | null> {
  return pickCurrentPod(await listStudioPods());
}

export async function getBalance(): Promise<number> {
  const data = await runpodGraphQL<{ myself: { clientBalance: number } }>("query { myself { clientBalance } }");
  return data.myself.clientBalance;
}

export function podApiBase(podId: string): string {
  return `https://${podId}-8000.proxy.runpod.net/api`;
}

// Acha o IP/porta publicos do SSH (porta 22 interna). Retorna null se o pod
// ainda estiver inicializando (portMappings vazio).
export function findSshEndpoint(pod: StudioPod): { host: string; port: number } | null {
  const port = pod.portMappings?.["22"];
  if (!pod.publicIp || !port) return null;
  return { host: pod.publicIp, port };
}

export async function stopPod(podId: string): Promise<void> {
  await rest("POST", `/pods/${podId}/stop`);
}

async function startPod(podId: string): Promise<void> {
  await rest("POST", `/pods/${podId}/start`);
}

async function terminatePod(podId: string): Promise<void> {
  await rest("DELETE", `/pods/${podId}`);
}

function podEnv(volume: StudioVolume): Record<string, string> {
  const peer = VOLUMES.find((v) => v.id !== volume.id);
  const env: Record<string, string> = {
    PUBLIC_KEY: SSH_PUBLIC_KEY,
    LUNA_SELF_VOLUME_ID: volume.id,
    LUNA_SELF_DATACENTER: volume.dataCenterId,
  };
  if (peer) {
    env.LUNA_PEER_VOLUME_ID = peer.id;
    env.LUNA_PEER_DATACENTER = peer.dataCenterId;
  }
  // Credenciais S3 da RunPod para o pod sincronizar os volumes. Sem elas o
  // estudio funciona igual, so nao mantem os dois volumes iguais.
  if (process.env.RUNPOD_S3_ACCESS_KEY && process.env.RUNPOD_S3_SECRET_KEY) {
    env.RUNPOD_S3_ACCESS_KEY = process.env.RUNPOD_S3_ACCESS_KEY;
    env.RUNPOD_S3_SECRET_KEY = process.env.RUNPOD_S3_SECRET_KEY;
  }
  return env;
}

async function deployPod(volume: StudioVolume, gpuTypeIds: string[]): Promise<StudioPod> {
  const created = await rest<RestPod>("POST", "/pods", {
    name: `${STUDIO_POD_PREFIX}-${volume.dataCenterId.toLowerCase()}`,
    imageName: IMAGE_NAME,
    cloudType: "SECURE",
    computeType: "GPU",
    gpuCount: 1,
    gpuTypeIds,
    gpuTypePriority: "custom",
    dataCenterIds: [volume.dataCenterId],
    networkVolumeId: volume.id,
    volumeMountPath: "/workspace",
    containerDiskInGb: 60,
    minRAMPerGPU: 16,
    allowedCudaVersions: ["12.8", "12.9", "13.0"],
    ports: ["8188/http", "8000/http", "8888/http", "8080/http", "22/tcp"],
    env: podEnv(volume),
    dockerEntrypoint: DOCKER_ENTRYPOINT,
  });
  return toStudioPod({ ...created, networkVolume: { id: volume.id, dataCenterId: volume.dataCenterId } });
}

export interface WakeResult {
  alreadyRunning: boolean;
  action: "running" | "resumed" | "created";
  pod: StudioPod;
  attempts: string[];
}

export async function wakeStudio(): Promise<WakeResult> {
  const cap = maxPricePerHr();
  const pods = await listStudioPods();
  const attempts: string[] = [];

  const running = pods.find((p) => p.desiredStatus === "RUNNING");
  if (running) {
    return { alreadyRunning: true, action: "running", pod: running, attempts };
  }

  // 1. Religar um pod parado (volume preferido primeiro).
  const stopped = pods
    .filter((p) => p.desiredStatus === "EXITED")
    .sort((a, b) => volumeRank(a) - volumeRank(b));
  const stuck: StudioPod[] = [];
  for (const pod of stopped) {
    if (pod.costPerHr > cap) {
      attempts.push(`${pod.id}: custa $${pod.costPerHr}/h, acima do teto $${cap}/h`);
      stuck.push(pod);
      continue;
    }
    try {
      await startPod(pod.id);
      return { alreadyRunning: false, action: "resumed", pod: { ...pod, desiredStatus: "RUNNING" }, attempts };
    } catch (err) {
      attempts.push(`religar ${pod.id} (${pod.dataCenterId}): ${err instanceof Error ? err.message : err}`);
      stuck.push(pod);
    }
  }

  // 2. Criar um pod novo, GPU mais barata primeiro, em cada volume.
  const gpuTypeIds = GPU_OPTIONS.filter((g) => g.pricePerHr <= cap)
    .sort((a, b) => a.pricePerHr - b.pricePerHr)
    .map((g) => g.id);
  for (const volume of VOLUMES) {
    try {
      const pod = await deployPod(volume, gpuTypeIds);
      // Os pods que nao religaram ficaram presos a uma maquina lotada e nao
      // guardam nada (tudo fica no volume) - apagar evita acumular pods
      // parados e garante que o proximo "ligar" nao tente eles de novo.
      await Promise.allSettled(stuck.map((p) => terminatePod(p.id)));
      return { alreadyRunning: false, action: "created", pod, attempts };
    } catch (err) {
      attempts.push(`criar em ${volume.dataCenterId}: ${err instanceof Error ? err.message : err}`);
    }
  }

  throw new NoGpuAvailableError(
    `There are not enough free GPUs (ate $${cap}/h) em nenhum dos volumes. Tentativas: ${attempts.join(" | ")}`
  );
}
