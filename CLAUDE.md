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
Tabela `users`: `id`, `username` (único), `senha_hash`, `criado_em`, `is_admin`
(0/1). O **primeiro** usuário cadastrado vira admin; `init_db()` também promove o
usuário mais antigo se nenhum admin existir (cobre o deploy de bancos antigos).

**Admin:** decorator `admin_required` (consulta `is_admin` no banco, não na sessão).
Painel `/admin`: resetar senha, excluir usuário (cascata em transacoes/orcamentos/
recorrentes), promover/rebaixar admin. Proteções: não excluir a si mesmo, não remover
o último admin. Qualquer usuário troca a própria senha em `/trocar-senha`.
Recuperação por e-mail foi descartada (PythonAnywhere free bloqueia envio).

Tabela `orcamentos`: `id`, `usuario_id`, `categoria`, `limite` — UNIQUE(usuario_id,
categoria). Limite de gasto mensal por categoria (upsert via ON CONFLICT). A home
compara com o gasto do mês (verde/amarelo/vermelho). Rotas `/orcamentos` (GET/POST e
`/orcamentos/excluir/<id>`).

Tabela `recorrentes`: modelo que gera 1 transação/mês (`descricao`, `valor`, `tipo`,
`categoria`, `forma`, `detalhes`, `dia`, `ultimo_mes_gerado`). `gerar_recorrentes()`
roda ao abrir a home (e ao criar): cria o lançamento do mês atual se
`ultimo_mes_gerado < AAAA-MM`. Sem backfill de meses pulados. Rotas `/recorrentes`
(GET/POST e `/recorrentes/excluir/<id>`); excluir o recorrente mantém os lançamentos.

Tabela `transacoes`:
- `id` INTEGER PK AUTOINCREMENT
- `descricao` TEXT
- `valor` REAL (sempre positivo; o sinal vem do `tipo`)
- `tipo` TEXT — `RECEITA` ou `DESPESA`
- `categoria` TEXT
- `data` TEXT (`AAAA-MM-DD`)
- `usuario_id` INTEGER → dono da transação (FK lógica para `users.id`)
- `forma` TEXT → só para GASTOS: `CREDITO` (a pagar, cai na fatura) ou `DEBITO`
  (já pago). Ganhos e dados antigos ficam NULL = tratados como "já pago".
- `detalhes` TEXT → anotação opcional do usuário (o que foi comprado). Vazio vira
  NULL. Aparece no extrato só quando preenchido.

**UI:** "Ganho"/"Gasto" são só rótulos de tela; no banco continuam `RECEITA`/`DESPESA`.
A home mostra "Já pago" (débito + NULL) vs "Falta pagar" (crédito) do período, pra
fechar o mês. O campo de forma só aparece quando o tipo é Gasto. O seletor de tipo
são dois botões (radio estilizado), CSS em `base.html`.

**Categoria:** campo de texto livre com `<datalist>` das categorias que o próprio
usuário já usou (lista personalizada auto-construída) — passado como `categorias`
pelas rotas `/`, `/editar`, `/orcamentos` e `/recorrentes`. Descrição = livre (item);
detalhes = nota opcional.

**Extrato:** tem busca (`q` em descrição/categoria/detalhes) + filtros por categoria
(`cat`) e forma (`forma`). Os filtros afetam só a lista; cards/gráfico seguem o mês.

**Parcelas:** gasto no crédito aceita `parcelas` (Nx) → cria N transações nos meses
seguintes (helper `add_months`), sobra de centavos na 1ª. Sufixo `(i/N)` na descrição.

**Dinheiro:** filtro `|moeda` formata no padrão BR (1.000.000,00). Usar SEMPRE nos
valores exibidos. Inputs `type=number` continuam com ponto (sem separador).

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
6. [x] Ganho/Gasto + crédito/débito (já pago vs falta pagar) + detalhes opcionais + moeda BR
7. [x] Busca/filtros no extrato, orçamento por categoria, parcelas no crédito, lançamentos recorrentes

## Produção
- **No ar em:** https://gustavoaw.pythonanywhere.com (PythonAnywhere, plano grátis)
- Passo a passo completo em `DEPLOY.md`.
- **Atualizar o site após mexer no código:** no console Bash do PythonAnywhere,
  `cd whereMyCash && git pull`, depois **Reload** na aba Web.
- O `database.db` de produção é separado do local — fica no servidor.
- Plano grátis: clicar em "Run until 3 months from today" na aba Web a cada ~3 meses.
