# Como publicar o whereMyCash no PythonAnywhere (grátis)

Guia para deixar o site no ar e acessível pelo celular, de qualquer lugar.
Substitua `SEU_USUARIO` pelo nome de usuário que você escolher no PythonAnywhere.

## 1. Criar a conta (grátis)
1. Acesse https://www.pythonanywhere.com/
2. Clique em **Pricing & signup** → **Create a Beginner account** (plano gratuito).
3. Escolha um **username** — ele vira o endereço do site: `SEU_USUARIO.pythonanywhere.com`.
4. Confirme o e-mail.

## 2. Baixar o código do GitHub
1. No painel, vá em **Consoles** → **Bash** (abre um terminal no navegador).
2. Rode:
   ```bash
   git clone https://github.com/wotocoskig/whereMyCash.git
   ```

## 3. Criar o ambiente e instalar o Flask
Ainda no console Bash:
```bash
mkvirtualenv --python=/usr/bin/python3.10 venv-cash
pip install -r whereMyCash/requirements.txt
```
> Anote o caminho do virtualenv: `/home/SEU_USUARIO/.virtualenvs/venv-cash`

## 4. Criar o Web App
1. Vá na aba **Web** → **Add a new web app** → **Next**.
2. Escolha **Manual configuration** (NÃO escolha "Flask").
3. Selecione **Python 3.10**.

## 5. Configurar o Web App (aba Web)
- **Source code:** `/home/SEU_USUARIO/whereMyCash`
- **Working directory:** `/home/SEU_USUARIO/whereMyCash`
- **Virtualenv:** `/home/SEU_USUARIO/.virtualenvs/venv-cash`

## 6. Editar o arquivo WSGI
1. Na aba **Web**, clique no link do **WSGI configuration file**
   (algo como `/var/www/SEU_USUARIO_pythonanywhere_com_wsgi.py`).
2. **Apague tudo** e cole exatamente isto (trocando `SEU_USUARIO`):
   ```python
   import sys

   path = '/home/SEU_USUARIO/whereMyCash'
   if path not in sys.path:
       sys.path.insert(0, path)

   from app import app as application
   ```
3. Salve (**Save**).

## 7. Subir o site
1. Volte na aba **Web** e clique no botão verde **Reload**.
2. Abra `https://SEU_USUARIO.pythonanywhere.com` — no PC e no celular. 🎉

## Atualizar o site depois (quando mudar o código)
No console Bash:
```bash
cd whereMyCash
git pull
```
Depois clique em **Reload** na aba Web.

## Observações
- O banco `database.db` é criado automaticamente no primeiro acesso, **lá no servidor**
  (separado do banco do seu PC). Os dados ficam salvos no PythonAnywhere.
- No plano grátis, a cada ~3 meses aparece um botão **"Run until 3 months from today"**
  na aba Web — é só clicar pra manter o site no ar.
