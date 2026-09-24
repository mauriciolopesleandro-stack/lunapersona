// Senha de login. Enquanto ninguem trocou a senha pelo app, vale a
// APP_LOGIN_PASSWORD da Vercel. Depois da primeira troca, vale o hash
// guardado no armazenamento (_store.ts) e a env var deixa de ser usada.
import { randomBytes, scrypt, timingSafeEqual } from "crypto";
import { safeEqual } from "./_auth.js";
import { storeConfigured, storeGet, storeSet } from "./_store.js";

const PASSWORD_KEY = "luna:login_password";
const KEY_LENGTH = 64;
export const MIN_PASSWORD_LENGTH = 8;

function scryptAsync(password: string, salt: Buffer): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    scrypt(password, salt, KEY_LENGTH, (err, key) => (err ? reject(err) : resolve(key)));
  });
}

// Formato guardado: "scrypt$<salt hex>$<hash hex>"
async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16);
  const hash = await scryptAsync(password, salt);
  return `scrypt$${salt.toString("hex")}$${hash.toString("hex")}`;
}

async function matchesHash(password: string, stored: string): Promise<boolean> {
  const [scheme, saltHex, hashHex] = stored.split("$");
  if (scheme !== "scrypt" || !saltHex || !hashHex) return false;
  const expected = Buffer.from(hashHex, "hex");
  const actual = await scryptAsync(password, Buffer.from(saltHex, "hex"));
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}

// Lanca erro se o armazenamento estiver configurado mas fora do ar - nesse
// caso NAO cai para a env var, senao a senha antiga voltaria a valer.
export async function checkPassword(password: string): Promise<boolean> {
  if (!password) return false;
  if (storeConfigured()) {
    const stored = await storeGet(PASSWORD_KEY);
    if (stored) return matchesHash(password, stored);
  }
  const envPassword = process.env.APP_LOGIN_PASSWORD;
  return Boolean(envPassword) && safeEqual(password, envPassword as string);
}

export async function setPassword(password: string): Promise<void> {
  await storeSet(PASSWORD_KEY, await hashPassword(password));
}
