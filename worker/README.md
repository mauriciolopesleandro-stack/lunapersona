# Automação local da Naiaflow (parte B)

Gera na Naiaflow as fotos dos planos aprovados no Luna Studio (Assistente → Planejamento) e manda cada uma para você aprovar no Telegram.

1. No estúdio: **Configurações → Automação do PC → Conectar este computador**. Baixa o `luna-worker.env`.
2. Mova o `luna-worker.env` para esta pasta (`worker`).
3. **Só na primeira vez** (ou se a sessão da Naiaflow expirar): dê dois cliques em **Entrar na Naiaflow.bat**, entre com a sua conta Google no Chrome que abrir e, quando aparecer a tela de geração, feche esse Chrome. O login precisa ser feito por esse atalho: o Google bloqueia o login com conta Google no Chrome controlado pelo robô ("Esse navegador ou app pode não ser seguro").
4. Dê dois cliques em **Iniciar automação.bat** e deixe a janela preta aberta. O robô usa a sessão salva no passo 3.

Precisa do Python 3.11 e do Google Chrome instalados. O programa usa o seu Chrome, num perfil separado (`.perfil-naiaflow`), e para sozinho quando a Naiaflow tem menos de 2 créditos.

Registro do que aconteceu: `luna-worker.log`. Nunca compartilhe o `luna-worker.env` (é a chave do seu computador; para trocar, gere outra no estúdio).
