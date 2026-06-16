import sqlite3
from datetime import date, datetime
from flask import Flask, render_template, request, redirect

app = Flask(__name__)

DB = "database.db"


def get_db():
    """Abre conexão com o banco e devolve linhas acessíveis por nome de coluna."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Cria a tabela, faz migração da coluna 'data' e popula exemplos na 1ª vez."""
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transacoes (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao TEXT    NOT NULL,
            valor     REAL    NOT NULL,
            tipo      TEXT    NOT NULL,
            categoria TEXT    NOT NULL
        )
    """)

    # Migração: adiciona a coluna 'data' se ainda não existir (bancos antigos)
    colunas = [c["name"] for c in conn.execute("PRAGMA table_info(transacoes)")]
    if "data" not in colunas:
        conn.execute("ALTER TABLE transacoes ADD COLUMN data TEXT")
        # Lançamentos antigos sem data recebem a data de hoje
        conn.execute(
            "UPDATE transacoes SET data = ? WHERE data IS NULL",
            (date.today().isoformat(),),
        )

    # Se a tabela estiver vazia, insere as transações de exemplo (seed inicial)
    total = conn.execute("SELECT COUNT(*) FROM transacoes").fetchone()[0]
    if total == 0:
        hoje = date.today().isoformat()
        exemplos = [
            ("Salário", 3000.00, "RECEITA", "Renda", hoje),
            ("Mercado", 500.00, "DESPESA", "Alimentação", hoje),
            ("Ifood", 80.00, "DESPESA", "Alimentação", hoje),
            ("Cinema", 60.00, "DESPESA", "Lazer", hoje),
        ]
        conn.executemany(
            "INSERT INTO transacoes (descricao, valor, tipo, categoria, data) "
            "VALUES (?, ?, ?, ?, ?)",
            exemplos,
        )

    conn.commit()
    conn.close()


# ---- Filtros de template (formatação amigável) ----

MESES_PT = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


@app.template_filter("data_br")
def data_br(valor):
    """Converte 'AAAA-MM-DD' para 'DD/MM/AAAA'. Tolerante a valores vazios."""
    if not valor:
        return ""
    try:
        return datetime.strptime(valor, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return valor


@app.template_filter("mes_br")
def mes_br(aaaa_mm):
    """Converte 'AAAA-MM' para 'Mês/AAAA' (ex: 'Junho/2026')."""
    try:
        ano, mes = aaaa_mm.split("-")
        return f"{MESES_PT[int(mes)]}/{ano}"
    except (ValueError, IndexError):
        return aaaa_mm


@app.route('/')
def index():
    mes_sel = request.args.get('mes', 'todos')

    conn = get_db()
    todas = conn.execute(
        "SELECT * FROM transacoes ORDER BY data DESC, id DESC"
    ).fetchall()
    conn.close()

    # Lista de meses disponíveis (AAAA-MM) para o filtro, do mais recente ao mais antigo
    meses = sorted({t["data"][:7] for t in todas if t["data"]}, reverse=True)

    # Aplica o filtro de mês (se não for "todos")
    if mes_sel != 'todos':
        transacoes = [t for t in todas if t["data"] and t["data"].startswith(mes_sel)]
    else:
        transacoes = todas

    # 1. Saldo do período exibido
    saldo = sum(
        t["valor"] if t["tipo"] == "RECEITA" else -t["valor"]
        for t in transacoes
    )

    # 2. Gastos por categoria (só DESPESA)
    gastos = {}
    for t in transacoes:
        if t["tipo"] == "DESPESA":
            gastos[t["categoria"]] = gastos.get(t["categoria"], 0) + t["valor"]

    # Prepara dados do gráfico: lista ordenada com percentual relativo ao maior gasto
    maior = max(gastos.values()) if gastos else 0
    total_despesas = sum(gastos.values())
    resumo = [
        {
            "categoria": cat,
            "total": total,
            "pct_barra": (total / maior * 100) if maior else 0,
            "pct_total": (total / total_despesas * 100) if total_despesas else 0,
        }
        for cat, total in sorted(gastos.items(), key=lambda x: x[1], reverse=True)
    ]

    return render_template(
        'index.html',
        transacoes=transacoes,
        saldo=saldo,
        resumo=resumo,
        meses=meses,
        mes_sel=mes_sel,
        hoje=date.today().isoformat(),
    )


@app.route('/adicionar', methods=['POST'])
def adicionar():
    descricao = request.form['descricao']
    valor = float(request.form['valor'])
    tipo = request.form['tipo']
    categoria = request.form['categoria']
    data_lanc = request.form.get('data') or date.today().isoformat()

    conn = get_db()
    conn.execute(
        "INSERT INTO transacoes (descricao, valor, tipo, categoria, data) "
        "VALUES (?, ?, ?, ?, ?)",
        (descricao, valor, tipo, categoria, data_lanc),
    )
    conn.commit()
    conn.close()

    return redirect('/')


@app.route('/excluir/<int:id>', methods=['POST'])
def excluir(id):
    conn = get_db()
    conn.execute("DELETE FROM transacoes WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect('/')


@app.route('/editar/<int:id>', methods=['GET', 'POST'])
def editar(id):
    conn = get_db()

    # POST: salva as alterações
    if request.method == 'POST':
        conn.execute(
            "UPDATE transacoes SET descricao = ?, valor = ?, tipo = ?, "
            "categoria = ?, data = ? WHERE id = ?",
            (
                request.form['descricao'],
                float(request.form['valor']),
                request.form['tipo'],
                request.form['categoria'],
                request.form.get('data') or date.today().isoformat(),
                id,
            ),
        )
        conn.commit()
        conn.close()
        return redirect('/')

    # GET: mostra o formulário pré-preenchido
    transacao = conn.execute(
        "SELECT * FROM transacoes WHERE id = ?", (id,)
    ).fetchone()
    conn.close()

    if transacao is None:
        return redirect('/')

    return render_template('editar.html', t=transacao)


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
