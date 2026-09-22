import type { IncomingMessage, ServerResponse } from "http";
import { Client } from "ssh2";
import { findSshPort, getPodAndBalance, RunpodConfigError } from "./_runpod.js";

// POST /api/runpod-bootstrap
// Roda "git pull && bash scripts/runpod_bootstrap.sh" dentro do pod via SSH,
// chamado pelo frontend logo depois que o pod acorda de um cold start -
// substitui o passo manual de abrir o Jupyter e rodar os comandos a mao.
//
// Exige duas variaveis extras na Vercel (alem de RUNPOD_API_KEY/RUNPOD_POD_ID):
//   RUNPOD_SSH_PRIVATE_KEY - a chave privada gerada para essa automacao
//   RUNPOD_SSH_USER        - opcional, default "root"
//
// A chave publica correspondente precisa estar na env var PUBLIC_KEY do pod
// (nao e segredo - o proprio start.sh da imagem le essa variavel e autoriza
// a chave em ~/.ssh/authorized_keys no boot).
const REMOTE_COMMAND = "cd /workspace/lunapersona && git pull && bash scripts/runpod_bootstrap.sh";
const SSH_CONNECT_TIMEOUT_MS = 15_000;
const COMMAND_TIMEOUT_MS = 4 * 60_000;

interface RemoteResult {
  stdout: string;
  stderr: string;
  code: number | null;
}

function runRemoteCommand(host: string, port: number, privateKey: string, username: string): Promise<RemoteResult> {
  return new Promise((resolve, reject) => {
    const conn = new Client();
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      conn.end();
      reject(new Error("Timeout esperando o comando terminar no pod."));
    }, COMMAND_TIMEOUT_MS);

    conn
      .on("ready", () => {
        conn.exec(REMOTE_COMMAND, (err, stream) => {
          if (err) {
            clearTimeout(timer);
            conn.end();
            reject(err);
            return;
          }
          stream
            .on("close", (code: number | null) => {
              clearTimeout(timer);
              conn.end();
              resolve({ stdout, stderr, code });
            })
            .on("data", (data: Buffer) => {
              stdout += data.toString();
            })
            .stderr.on("data", (data: Buffer) => {
              stderr += data.toString();
            });
        });
      })
      .on("error", (err) => {
        clearTimeout(timer);
        reject(err);
      })
      .connect({
        host,
        port,
        username,
        privateKey,
        readyTimeout: SSH_CONNECT_TIMEOUT_MS,
      });
  });
}

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  if (req.method !== "POST") {
    res.statusCode = 405;
    res.end(JSON.stringify({ detail: "Method not allowed" }));
    return;
  }

  res.setHeader("Content-Type", "application/json");

  try {
    const privateKey = process.env.RUNPOD_SSH_PRIVATE_KEY;
    if (!privateKey) {
      throw new RunpodConfigError("RUNPOD_SSH_PRIVATE_KEY nao configurada na Vercel.");
    }
    const username = process.env.RUNPOD_SSH_USER || "root";

    const data = await getPodAndBalance();
    if (!data.pod || data.pod.desiredStatus !== "RUNNING") {
      res.statusCode = 409;
      res.end(JSON.stringify({ detail: "Pod nao esta rodando." }));
      return;
    }

    const sshPort = findSshPort(data.pod.runtime?.ports);
    if (!sshPort) {
      res.statusCode = 409;
      res.end(JSON.stringify({ detail: "Porta SSH (22/tcp) ainda nao disponivel - o pod pode estar inicializando." }));
      return;
    }

    const result = await runRemoteCommand(sshPort.ip, sshPort.publicPort, privateKey, username);
    res.statusCode = 200;
    res.end(
      JSON.stringify({
        ok: result.code === 0,
        exitCode: result.code,
        stdout: result.stdout.slice(-4000),
        stderr: result.stderr.slice(-4000),
      })
    );
  } catch (err) {
    res.statusCode = err instanceof RunpodConfigError ? 501 : 502;
    res.end(JSON.stringify({ detail: err instanceof Error ? err.message : String(err) }));
  }
}
