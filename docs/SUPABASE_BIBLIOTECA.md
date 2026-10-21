# Biblioteca de conteúdo (Fase 2): como ligar o Supabase

A aba **Conteúdos** guarda imagens e vídeos num projeto Supabase privado. Enquanto o Supabase não estiver ligado, a aba só mostra o aviso "Falta ligar o armazenamento"; o resto do site funciona igual.

## Como funciona

```
navegador ──(sessão luna_session)──> /api/media (Vercel) ──(chave secreta)──> Supabase Postgres + Storage
navegador ──(URL assinada, 2 h)──────────────────────────────────────────────> Storage (envio direto do arquivo)
navegador <─(URL assinada de leitura, 1 h)──────────────────────────────────── Storage
```

- **Login:** é o mesmo de hoje. `/api/media` recusa quem não tem sessão válida.
- **Chave secreta:** fica só na Vercel. O navegador nunca recebe chave do Supabase.
- **Envio de arquivos:** o arquivo vai do navegador direto para o Storage. Assim, vídeo grande não passa pela Vercel, que tem limite de tamanho por requisição.
- **Bucket `luna-media`:** é privado. A leitura só funciona por link assinado, que vence em 1 hora.
- **Tabelas `media` e `audit_log`:**
  - o RLS fica ligado, sem nenhuma política, então `anon`/`authenticated` não acessam nada;
  - o app não consegue apagar: só arquiva e restaura;
  - apagar de verdade é só pelo painel do Supabase.
- **Histórico de operações:** cada envio, arquivamento e restauração fica registrado em `audit_log`.
- **Arquivo repetido:** é detectado pela impressão digital (SHA-256). O app avisa e pergunta se é para enviar mesmo assim.
- **Nome no armazenamento:** o arquivo é gravado como `ano/mês/<id>.<ext>`. O nome original fica só no banco.

## Passo a passo (feito por você: as chaves nunca passam por mim)

1. **Criar o projeto Supabase.** Em https://supabase.com, crie um projeto novo. Região recomendada: South America (São Paulo).
2. **Criar as tabelas e o bucket.** No painel do Supabase, abra **SQL Editor**, cole todo o conteúdo de `supabase/migrations/0001_biblioteca.sql` e clique em **Run**.
   - Pode rodar de novo sem problema.
   - Para conferir: em **Storage** deve aparecer o bucket `luna-media` marcado como privado.
3. **Copiar os dados de acesso.** Em **Project Settings → API Keys**, copie:
   - a **Project URL** (`https://xxxx.supabase.co`);
   - uma **Secret key** (`sb_secret_...`).
   - Use a secret key, **não** a publishable.
4. **Cadastrar na Vercel.** No projeto, abra **Settings → Environment Variables** e adicione, marcando Production (e Preview, se quiser testar antes):
   - `SUPABASE_URL` = a Project URL;
   - `SUPABASE_SECRET_KEY` = a secret key.
   - Não use o prefixo `VITE_`: ele mandaria a chave para o navegador.
5. **Fazer um novo deploy** (Deployments → Redeploy) para as variáveis valerem.

## Planos e limites (fonte: supabase.com/pricing, consultado em 21/10/2026)

| | Free | Pro (a partir de US$ 25/mês) |
|---|---|---|
| Armazenamento de arquivos | 1 GB | 100 GB (+US$ 0,0213/GB) |
| Tamanho máximo por arquivo | 50 MB | até 500 GB |
| Tráfego de saída | 5 GB/mês | 250 GB/mês |
| Banco | 500 MB | 8 GB |
| Pausa por inatividade | sim, após 1 semana | não |

**No plano Free:**
- vídeos acima de 50 MB são recusados;
- o projeto pausa se ficar uma semana sem uso. Ele volta pelo painel do Supabase.

**Para aumentar o limite por arquivo depois de passar para o Pro:**
1. suba o **Global file size limit** em Storage → Settings;
2. suba o `file_size_limit` do bucket;
3. mude `MEDIA_MAX_MB` na Vercel (o padrão é 50).

## Variáveis opcionais

- `MEDIA_MAX_MB`: limite por arquivo conferido pela Vercel (padrão `50`).
- `SUPABASE_MEDIA_BUCKET`: nome do bucket (padrão `luna-media`).
- `SUPABASE_SERVICE_ROLE_KEY`: alternativa à `SUPABASE_SECRET_KEY` para projetos que só têm a chave antiga (JWT `service_role`).

## Como desfazer

- Retirar `SUPABASE_URL` e `SUPABASE_SECRET_KEY` da Vercel e fazer um novo deploy. A aba volta a mostrar o aviso, e nada do Supabase é apagado.
- O código está todo em arquivos novos (`api/media.ts`, `api/_supabase.ts`, `src/components/LibraryPage.tsx`). Login, sessão e as outras funções não foram alterados.
