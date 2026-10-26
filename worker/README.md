# Automação local da Naiaflow (parte B)

Gera na Naiaflow as fotos dos planos aprovados no Luna Studio (Assistente → Planejamento) e manda cada uma para você aprovar no Telegram.

1. No estúdio: **Configurações → Automação do PC → Conectar este computador**. Baixa o `luna-worker.env`.
2. Mova o `luna-worker.env` para esta pasta (`worker`).
3. Dê dois cliques em **Iniciar automação.bat**. Na primeira vez abre um Chrome separado: entre na sua conta da Naiaflow nele. Deixe a janela preta aberta.

Precisa do Python 3.11 e do Google Chrome instalados. O programa usa o seu Chrome, num perfil separado (`.perfil-naiaflow`), e para sozinho quando a Naiaflow tem menos de 2 créditos.

Registro do que aconteceu: `luna-worker.log`. Nunca compartilhe o `luna-worker.env` (é a chave do seu computador; para trocar, gere outra no estúdio).
