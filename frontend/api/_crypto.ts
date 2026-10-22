// Cifra de tokens das redes (AES-256-GCM). A chave vem de TOKEN_ENCRYPTION_KEY
// na Vercel (so servidor); o banco guarda so o texto cifrado.
import { createCipheriv, createDecipheriv, createHash, randomBytes } from "crypto";

function key(): Buffer {
  const secret = process.env.TOKEN_ENCRYPTION_KEY;
  if (!secret || secret.length < 32) throw new Error("TOKEN_ENCRYPTION_KEY ausente ou curta (minimo 32 caracteres) na Vercel.");
  return createHash("sha256").update(secret).digest();
}

export function encryptionConfigured(): boolean {
  return Boolean(process.env.TOKEN_ENCRYPTION_KEY && process.env.TOKEN_ENCRYPTION_KEY.length >= 32);
}

export function encrypt(plain: string): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key(), iv);
  const data = Buffer.concat([cipher.update(plain, "utf8"), cipher.final()]);
  return ["v1", iv.toString("base64"), cipher.getAuthTag().toString("base64"), data.toString("base64")].join(":");
}

export function decrypt(sealed: string): string {
  const [version, iv, tag, data] = sealed.split(":");
  if (version !== "v1" || !iv || !tag || !data) throw new Error("Token cifrado em formato desconhecido.");
  const decipher = createDecipheriv("aes-256-gcm", key(), Buffer.from(iv, "base64"));
  decipher.setAuthTag(Buffer.from(tag, "base64"));
  return Buffer.concat([decipher.update(Buffer.from(data, "base64")), decipher.final()]).toString("utf8");
}
