import type { IncomingMessage, ServerResponse } from "http";
import { Client } from "ssh2";
import { isAuthenticated } from "./_auth.js";
import { findSshEndpoint, getCurrentPod, RunpodConfigError } from "./_runpod.js";

// POST /api/runpod-bootstrap
// Roda "git pull && bash scripts/runpod_bootstrap.sh" dentro do pod via SSH.
// Os pods criados por wakeStudio ja fazem isso sozinhos no boot
// (scripts/pod_autostart.sh) - este endpoint ficou como plano B manual,
// para quando o backend nao subir sozinho.
//
// Exige duas variaveis extras na Vercel (alem de RUNPOD_API_KEY):
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
// O "< /dev/null" e essencial: sem ele, o processo em background continua
// segurando o stdin herdado do canal SSH, e o exec nunca sinaliza "close"
// (ficamos esperando o script inteiro terminar mesmo com nohup + &).
const REMOTE_COMMAND =
  "cd /workspace/lunapersona && git pull && " +
  "mkdir -p /tmp/luna-logs && " +
  "nohup bash scripts/runpod_bootstrap.sh < /dev/null > /tmp/luna-logs/bootstrap.log 2>&1 & disown; " +
  "echo BOOTSTRAP_LAUNCHED";
// Testando ao vivo, o handshake demorou mais pela rede da Vercel do que
// direto da minha maquina (que conectou na hora) - 15s nao foi suficiente.
// COMMAND_TIMEOUT_MS tambem ja se mostrou curto demais: o comando remoto
// (git pull + lancar o bootstrap em background) as vezes passou de 25s so
// pra devolver o "echo" - a funcao dava "Timeout" no frontend mesmo com o
// bootstrap tendo sido disparado com sucesso do lado do pod. Os dois juntos
// (30s + 40s = 70s) ficam perto do limite de execucao de uma funcao
// serverless da Vercel no plano gratuito (60s) - por isso maxDuration no
// vercel.json tambem subiu.
const SSH_CONNECT_TIMEOUT_MS = 20_000;
const COMMAND_TIMEOUT_MS = 35_000;

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
  if (!isAuthenticated(req)) {
    res.statusCode = 401;
    res.end(JSON.stringify({ detail: "Sessao expirada. Entre de novo." }));
    return;
  }

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

    const pod = await getCurrentPod();
    if (!pod || pod.desiredStatus !== "RUNNING") {
      res.statusCode = 409;
      res.end(JSON.stringify({ detail: "Pod nao esta rodando." }));
      return;
    }

    const ssh = findSshEndpoint(pod);
    if (!ssh) {
      res.statusCode = 409;
      res.end(JSON.stringify({ detail: "Porta SSH (22/tcp) ainda nao disponivel - o pod pode estar inicializando." }));
      return;
    }

    const result = await runRemoteCommand(ssh.host, ssh.port, privateKey, username);
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
