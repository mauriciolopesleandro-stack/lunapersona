// Cliente minimo para a API GraphQL da RunPod, usado pelas funcoes serverless
// de wake/status. So roda no servidor (Vercel Functions) - a RUNPOD_API_KEY
// nunca chega ao bundle do navegador.
const RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql";

export class RunpodConfigError extends Error {}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new RunpodConfigError(`Variavel de ambiente ${name} nao configurada na Vercel.`);
  }
  return value;
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

export function podId(): string {
  return requireEnv("RUNPOD_POD_ID");
}

export interface PodPort {
  ip: string;
  isIpPublic: boolean;
  privatePort: number;
  publicPort: number;
  type: string;
}

export interface PodQueryResult {
  pod: {
    id: string;
    desiredStatus: string;
    costPerHr: string | number;
    runtime: { uptimeInSeconds: number; ports: PodPort[] | null } | null;
  } | null;
  myself: { clientBalance: number };
}

const POD_STATUS_QUERY = `
  query PodStatus($podId: String!) {
    pod(input: { podId: $podId }) {
      id
      desiredStatus
      costPerHr
      runtime {
        uptimeInSeconds
        ports { ip isIpPublic privatePort publicPort type }
      }
    }
    myself { clientBalance }
  }
`;

export async function getPodAndBalance() {
  return runpodGraphQL<PodQueryResult>(POD_STATUS_QUERY, { podId: podId() });
}

// Acha o mapeamento TCP publico da porta 22 (SSH) nas portas do runtime.
// Retorna null se o pod nao estiver rodando ou a porta 22 nao estiver exposta.
export function findSshPort(ports: PodPort[] | null | undefined): PodPort | null {
  if (!ports) return null;
  return ports.find((p) => p.privatePort === 22 && p.type === "tcp" && p.isIpPublic) ?? null;
}

const RESUME_MUTATION = `
  mutation ResumePod($podId: String!) {
    podResume(input: { podId: $podId }) {
      id
      desiredStatus
    }
  }
`;

export async function resumePod() {
  return runpodGraphQL<{ podResume: { id: string; desiredStatus: string } }>(RESUME_MUTATION, {
    podId: podId(),
  });
}

const STOP_MUTATION = `
  mutation StopPod($podId: String!) {
    podStop(input: { podId: $podId }) {
      id
      desiredStatus
    }
  }
`;

export async function stopPod() {
  return runpodGraphQL<{ podStop: { id: string; desiredStatus: string } }>(STOP_MUTATION, {
    podId: podId(),
  });
}
