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
// O bootstrap em si (reinstala Ollama/zstd/pciutils no container efemero,
// reusa modelo/venv do volume persistente) pode passar de 1-2 minutos -
// tempo demais pra uma funcao serverless esperar de forma sincrona. Por
// isso git pull roda em primeiro plano (rapido, só pra pegar o script mais
// recente) e o bootstrap em si e disparado em background (nohup) - a funcao
// so espera ele SER LANCADO, nao terminar.
const REMOTE_COMMAND =
  "cd /workspace/lunapersona && git pull && " +
  "mkdir -p /tmp/luna-logs && " +
  "nohup bash scripts/runpod_bootstrap.sh > /tmp/luna-logs/bootstrap.log 2>&1 & disown; " +
  "echo BOOTSTRAP_LAUNCHED";
// Testando ao vivo, o handshake demorou mais pela rede da Vercel do que
// direto da minha maquina (que conectou na hora) - 15s nao foi suficiente.
const SSH_CONNECT_TIMEOUT_MS = 30_000;
const COMMAND_TIMEOUT_MS = 25_000;

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
    const rawKey = process.env.RUNPOD_SSH_PRIVATE_KEY;
    if (!rawKey) {
      throw new RunpodConfigError("RUNPOD_SSH_PRIVATE_KEY nao configurada na Vercel.");
    }
    // Aceita tanto a chave OpenSSH crua (com quebras de linha reais) quanto,
    // preferencialmente, a versao em base64 - campos de env var de UI
    // costumam perder/escapar quebras de linha ao colar, o que quebra o
    // parser do ssh2 ("Malformed OpenSSH private key"). Base64 nao tem esse
    // problema por nao conter quebras de linha nenhuma.
    const privateKey = rawKey.trim().startsWith("-----BEGIN")
      ? rawKey
      : Buffer.from(rawKey, "base64").toString("utf8");
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
