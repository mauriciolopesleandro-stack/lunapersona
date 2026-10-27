# Automação local da Naiaflow (parte B)

Gera na Naiaflow as fotos dos planos aprovados no Luna Studio (Assistente → Planejamento) e manda cada uma para você aprovar no Telegram.

1. No estúdio: **Configurações → Automação do PC → Conectar este computador**. Baixa o `luna-worker.env`.
2. Mova o `luna-worker.env` para esta pasta (`worker`).
3. **Só na primeira vez** (ou se a sessão da Naiaflow expirar): dê dois cliques em **Entrar na Naiaflow.bat**, entre com a sua conta Google no Chrome que abrir e, quando aparecer a tela de geração, feche esse Chrome. O login precisa ser feito por esse atalho: o Google bloqueia o login com conta Google no Chrome controlado pelo robô ("Esse navegador ou app pode não ser seguro").
4. Dê dois cliques em **Iniciar automação.bat** e deixe a janela preta aberta. O robô usa a sessão salva no passo 3.
5. Para abrir sozinho quando o Windows ligar: atalho do **Iniciar automação.bat** na pasta Inicializar (`Win + R` → `shell:startup`). A janela abre minimizada; se ela cair (ex.: sem internet), tenta de novo em 2 minutos. Abrir de novo com uma já aberta não cria outra.

**Correção pelo Telegram:** em **✏️ Pedir correção**, escreva o que mudar (respondendo a pergunta ou direto no chat). Se a foto veio do robô, ele abre essa foto na Naiaflow, usa **Editar pelo texto** com o seu pedido (1 crédito) e a foto corrigida volta no Telegram para aprovar. Para achar a foto certa, o robô anota em `naia-jobs.json` qual job da Naiaflow gerou cada foto.

**Reels:** o robô gera a foto (1 crédito) e monta aqui no PC um vídeo 9:16 de 7 s com zoom leve (sem gastar crédito de vídeo). O estúdio escolhe uma música em alta da biblioteca do Instagram (precisa da conexão pelo Facebook); a música aparece na mensagem de aprovação. A correção de um Reels edita a foto e o vídeo é montado de novo.

Precisa do Python 3.11 e do Google Chrome instalados. O programa usa o seu Chrome, num perfil separado (`.perfil-naiaflow`), e para sozinho quando a Naiaflow tem menos de 2 créditos.

Registro do que aconteceu: `luna-worker.log`. Nunca compartilhe o `luna-worker.env` (é a chave do seu computador; para trocar, gere outra no estúdio).
