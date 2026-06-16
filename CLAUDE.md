# wheresMyCash

App web de **controle de gastos** ("onde está meu dinheiro"). **Multiusuário**: cada
pessoa tem login e senha e vê só os próprios lançamentos. O dono compartilha com amigos.

## Stack
- **Backend:** Python + Flask (`app.py`)
- **Auth:** sessões do Flask + senha com hash (werkzeug); `secret_key.txt` (não versionado)
- **Templates:** Jinja2 em `templates/` — `base.html` (layout + tema), `index.html`,
  `editar.html`, `login.html`, `registrar.html`
- **Banco:** SQLite em arquivo (`database.db`) — não versionado
- **Visual:** tema claro/escuro com toggle (salvo no localStorage); CSS com variáveis
- Idioma da UI e do código (variáveis/comentários): **Português (pt-BR)**

## Como rodar
```powershell
pip install -r requirements.txt
python app.py
```
Servidor de desenvolvimento em http://127.0.0.1:5000 (debug ligado).

## Modelo de dados
Tabela `users`: `id`, `username` (único), `senha_hash`, `criado_em`.

Tabela `transacoes`:
- `id` INTEGER PK AUTOINCREMENT
- `descricao` TEXT
- `valor` REAL (sempre positivo; o sinal vem do `tipo`)
- `tipo` TEXT — `RECEITA` ou `DESPESA`
- `categoria` TEXT
- `data` TEXT (`AAAA-MM-DD`)
- `usuario_id` INTEGER → dono da transação (FK lógica para `users.id`)

Toda query de transação é filtrada por `usuario_id = session['usuario_id']`
(inclui edição/exclusão, pra ninguém mexer no dado do outro). Migrações de coluna
são feitas no `init_db()` (idempotente). Ao cadastrar o **primeiro** usuário, ele
adota transações antigas sem dono (`usuario_id IS NULL`).

## Convenções
- Código e UI em português.
- Manter simples: o dono tem **pouco tempo**, prioriza entregas pequenas que já funcionam.
- Saldo = soma das RECEITAS menos soma das DESPESAS.
- Gastos agrupados por categoria (só DESPESA) alimentam o resumo da home.

## Contexto do usuário
- Trabalha com Oracle EBS / PL/SQL no dia a dia; confortável com SQL.
- Prefere que o Claude **implemente tudo e mostre pronto**, ele revisa o resultado.

## Roadmap (ordem de prioridade definida pelo dono)
1. [x] Persistência com SQLite (feito)
2. [x] Editar / excluir transações (feito — rotas /editar/<id> e /excluir/<id>, tela editar.html)
3. [x] Visual e relatórios (feito — gráfico de barras por categoria + filtro por mês; coluna `data` adicionada)
4. [x] Acessar pelo celular (feito — hospedado no PythonAnywhere)
5. [x] Multiusuário (login/cadastro, dados privados por usuário) + redesign moderno com tema claro/escuro

## Produção
- **No ar em:** https://gustavoaw.pythonanywhere.com (PythonAnywhere, plano grátis)
- Passo a passo completo em `DEPLOY.md`.
- **Atualizar o site após mexer no código:** no console Bash do PythonAnywhere,
  `cd whereMyCash && git pull`, depois **Reload** na aba Web.
- O `database.db` de produção é separado do local — fica no servidor.
- Plano grátis: clicar em "Run until 3 months from today" na aba Web a cada ~3 meses.
