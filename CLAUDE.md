# wheresMyCash

App web de **controle de gastos pessoal** do usuário ("onde está meu dinheiro"). Uso próprio, não comercial.

## Stack
- **Backend:** Python + Flask (`app.py`)
- **Templates:** Jinja2 em `templates/` (`index.html`)
- **Banco:** SQLite em arquivo (`database.db`) — não versionado
- Idioma da UI e do código (variáveis/comentários): **Português (pt-BR)**

## Como rodar
```powershell
pip install -r requirements.txt
python app.py
```
Servidor de desenvolvimento em http://127.0.0.1:5000 (debug ligado).

## Modelo de dados
Tabela `transacoes`:
- `id` INTEGER PK AUTOINCREMENT
- `descricao` TEXT
- `valor` REAL (sempre positivo; o sinal vem do `tipo`)
- `tipo` TEXT — `RECEITA` ou `DESPESA`
- `categoria` TEXT

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
4. [ ] Acessar pelo celular (hospedagem)
