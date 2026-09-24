// HEAD de um objeto na API S3 dos network volumes da RunPod, assinado com
// AWS Signature V4 na mao (so crypto do Node - sem SDK). Usado para saber se
// um volume ja recebeu a copia completa (marcador gravado por
// scripts/volume_sync.py) antes de subir o estudio nele.
import { createHash, createHmac } from "crypto";

function hmac(key: Buffer | string, data: string): Buffer {
  return createHmac("sha256", key).update(data).digest();
}

function sha256Hex(data: string): string {
  return createHash("sha256").update(data).digest("hex");
}

export function s3Configured(): boolean {
  return Boolean(process.env.RUNPOD_S3_ACCESS_KEY && process.env.RUNPOD_S3_SECRET_KEY);
}

// true = objeto existe; false = nao existe (ou S3 nao configurado).
export async function s3ObjectExists(dataCenterId: string, bucket: string, key: string): Promise<boolean> {
  const accessKey = process.env.RUNPOD_S3_ACCESS_KEY;
  const secretKey = process.env.RUNPOD_S3_SECRET_KEY;
  if (!accessKey || !secretKey) return false;

  const host = `s3api-${dataCenterId.toLowerCase()}.runpod.io`;
  const path = `/${bucket}/${key.split("/").map(encodeURIComponent).join("/")}`;
  const amzDate = new Date().toISOString().replace(/[:-]|\.\d{3}/g, "");
  const dateStamp = amzDate.slice(0, 8);
  const payloadHash = sha256Hex("");
  const signedHeaders = "host;x-amz-content-sha256;x-amz-date";
  const canonicalRequest = [
    "HEAD",
    path,
    "",
    `host:${host}\nx-amz-content-sha256:${payloadHash}\nx-amz-date:${amzDate}\n`,
    signedHeaders,
    payloadHash,
  ].join("\n");
  const scope = `${dateStamp}/${dataCenterId}/s3/aws4_request`;
  const stringToSign = ["AWS4-HMAC-SHA256", amzDate, scope, sha256Hex(canonicalRequest)].join("\n");
  const signingKey = hmac(hmac(hmac(hmac(`AWS4${secretKey}`, dateStamp), dataCenterId), "s3"), "aws4_request");
  const signature = createHmac("sha256", signingKey).update(stringToSign).digest("hex");

  const res = await fetch(`https://${host}${path}`, {
    method: "HEAD",
    headers: {
      "x-amz-content-sha256": payloadHash,
      "x-amz-date": amzDate,
      Authorization: `AWS4-HMAC-SHA256 Credential=${accessKey}/${scope}, SignedHeaders=${signedHeaders}, Signature=${signature}`,
    },
    signal: AbortSignal.timeout(8_000),
  });
  if (res.status === 200) return true;
  if (res.status === 404) return false;
  throw new Error(`S3 ${host} HEAD ${path} respondeu ${res.status}`);
}
