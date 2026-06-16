import os
import secrets
import sqlite3
from datetime import date, datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, session, flash, url_for
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Caminho absoluto do banco (funciona local e em hospedagem, independente do cwd).
DB = os.path.join(BASE_DIR, "database.db")
SECRET_FILE = os.path.join(BASE_DIR, "secret_key.txt")


def carregar_secret_key():
    """Lê a chave de sessão de um arquivo local; cria uma aleatória na 1ª vez.

    Mantida fora do Git (ver .gitignore) para não expor a chave no repositório.
    """
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "r") as f:
            return f.read().strip()
    chave = secrets.token_hex(32)
    with open(SECRET_FILE, "w") as f:
        f.write(chave)
    return chave


app.secret_key = carregar_secret_key()


def get_db():
    """Abre conexão com o banco e devolve linhas acessíveis por nome de coluna."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Cria/atualiza as tabelas. Idempotente — pode rodar a cada importação."""
    conn = get_db()

    # Usuários
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            username  TEXT    NOT NULL UNIQUE,
            senha_hash TEXT   NOT NULL,
            criado_em TEXT    NOT NULL
        )
    """)

    # Transações
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transacoes (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao TEXT    NOT NULL,
            valor     REAL    NOT NULL,
            tipo      TEXT    NOT NULL,
            categoria TEXT    NOT NULL
        )
    """)

    # Migrações de colunas adicionadas ao longo do tempo
    colunas = [c["name"] for c in conn.execute("PRAGMA table_info(transacoes)")]
    if "data" not in colunas:
        conn.execute("ALTER TABLE transacoes ADD COLUMN data TEXT")
        conn.execute(
            "UPDATE transacoes SET data = ? WHERE data IS NULL",
            (date.today().isoformat(),),
        )
    if "usuario_id" not in colunas:
        # Liga cada transação ao seu dono. Linhas antigas ficam sem dono (NULL)
        # e são adotadas pelo primeiro usuário que se cadastrar (ver /registrar).
        conn.execute("ALTER TABLE transacoes ADD COLUMN usuario_id INTEGER")
    if "forma" not in colunas:
        # Forma de pagamento dos GASTOS: 'CREDITO' (cai na fatura, ainda a pagar)
        # ou 'DEBITO' (já saiu da conta). Ganhos e dados antigos ficam NULL,
        # tratados como "já pago / à vista".
        conn.execute("ALTER TABLE transacoes ADD COLUMN forma TEXT")
    if "detalhes" not in colunas:
        # Anotação opcional do usuário sobre a compra (o que foi comprado etc.).
        conn.execute("ALTER TABLE transacoes ADD COLUMN detalhes TEXT")

    # Orçamentos: limite de gasto por categoria, por usuário (um por categoria).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orcamentos (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            categoria  TEXT    NOT NULL,
            limite     REAL    NOT NULL,
            UNIQUE (usuario_id, categoria)
        )
    """)

    conn.commit()
    conn.close()


# ---- Autenticação ----

def login_required(f):
    """Protege rotas: sem sessão, manda pro login."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


@app.route('/registrar', methods=['GET', 'POST'])
def registrar():
    if "usuario_id" in session:
        return redirect('/')

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        senha = request.form.get('senha', '')
        senha2 = request.form.get('senha2', '')

        if len(username) < 3:
            flash("O nome de usuário precisa ter pelo menos 3 caracteres.", "erro")
            return render_template('registrar.html', username=username)
        if len(senha) < 4:
            flash("A senha precisa ter pelo menos 4 caracteres.", "erro")
            return render_template('registrar.html', username=username)
        if senha != senha2:
            flash("As senhas não conferem.", "erro")
            return render_template('registrar.html', username=username)

        conn = get_db()
        existe = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existe:
            conn.close()
            flash("Esse nome de usuário já está em uso.", "erro")
            return render_template('registrar.html', username=username)

        cur = conn.execute(
            "INSERT INTO users (username, senha_hash, criado_em) VALUES (?, ?, ?)",
            (username, generate_password_hash(senha), date.today().isoformat()),
        )
        novo_id = cur.lastrowid

        # Se for o primeiro usuário, adota transações antigas que não tinham dono.
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if total_users == 1:
            conn.execute(
                "UPDATE transacoes SET usuario_id = ? WHERE usuario_id IS NULL",
                (novo_id,),
            )

        conn.commit()
        conn.close()

        session['usuario_id'] = novo_id
        session['usuario_nome'] = username
        flash("Conta criada com sucesso! Bem-vindo(a). 🎉", "ok")
        return redirect('/')

    return render_template('registrar.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if "usuario_id" in session:
        return redirect('/')

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        senha = request.form.get('senha', '')

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()

        if user is None or not check_password_hash(user["senha_hash"], senha):
            flash("Usuário ou senha inválidos.", "erro")
            return render_template('login.html', username=username)

        session['usuario_id'] = user["id"]
        session['usuario_nome'] = user["username"]
        return redirect('/')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


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


@app.template_filter("moeda")
def moeda(valor):
    """Formata número no padrão brasileiro: 1000000 -> '1.000.000,00'."""
    try:
        v = float(valor)
    except (TypeError, ValueError):
        v = 0.0
    # f"{v:,.2f}" usa padrão US (1,000,000.00); troca-se ',' e '.' para pt-BR.
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ---- Rotas principais (todas exigem login) ----

@app.route('/')
@login_required
def index():
    mes_sel = request.args.get('mes', 'todos')
    uid = session['usuario_id']

    conn = get_db()
    todas = conn.execute(
        "SELECT * FROM transacoes WHERE usuario_id = ? ORDER BY data DESC, id DESC",
        (uid,),
    ).fetchall()
    orcamentos_db = conn.execute(
        "SELECT categoria, limite FROM orcamentos WHERE usuario_id = ?",
        (uid,),
    ).fetchall()
    conn.close()

    # Meses disponíveis para o filtro (do mais recente ao mais antigo)
    meses = sorted({t["data"][:7] for t in todas if t["data"]}, reverse=True)

    # Lista personalizada de categorias: as que o próprio usuário já usou.
    categorias = sorted({t["categoria"] for t in todas if t["categoria"]}, key=str.lower)

    # Recorte do mês: alimenta os cards de resumo, o gráfico e o status de pagamento.
    if mes_sel != 'todos':
        tx_mes = [t for t in todas if t["data"] and t["data"].startswith(mes_sel)]
    else:
        tx_mes = todas

    total_receitas = sum(t["valor"] for t in tx_mes if t["tipo"] == "RECEITA")
    total_despesas = sum(t["valor"] for t in tx_mes if t["tipo"] == "DESPESA")
    saldo = total_receitas - total_despesas

    # Status de pagamento dos gastos: crédito = ainda a pagar; o resto já foi pago
    # (débito ou lançamentos antigos sem forma definida).
    total_a_pagar = sum(
        t["valor"] for t in tx_mes
        if t["tipo"] == "DESPESA" and t["forma"] == "CREDITO"
    )
    total_pago = total_despesas - total_a_pagar
    pct_pago = (total_pago / total_despesas * 100) if total_despesas else 0

    # Gastos por categoria (só DESPESA) para o gráfico
    gastos = {}
    for t in tx_mes:
        if t["tipo"] == "DESPESA":
            gastos[t["categoria"]] = gastos.get(t["categoria"], 0) + t["valor"]

    maior = max(gastos.values()) if gastos else 0
    resumo = [
        {
            "categoria": cat,
            "total": total,
            "pct_barra": (total / maior * 100) if maior else 0,
            "pct_total": (total / total_despesas * 100) if total_despesas else 0,
        }
        for cat, total in sorted(gastos.items(), key=lambda x: x[1], reverse=True)
    ]

    # Orçamentos: compara o gasto do mês na categoria com o limite definido.
    orcamentos_status = []
    for o in orcamentos_db:
        gasto = gastos.get(o["categoria"], 0)
        limite = o["limite"]
        pct = (gasto / limite * 100) if limite else 0
        orcamentos_status.append({
            "categoria": o["categoria"],
            "gasto": gasto,
            "limite": limite,
            "pct": pct,
            "pct_barra": min(pct, 100),
            "estourou": gasto > limite,
            "restante": limite - gasto,
        })
    orcamentos_status.sort(key=lambda x: x["pct"], reverse=True)

    # Extrato: parte do recorte do mês e aplica busca + filtros de categoria/forma.
    q = request.args.get('q', '').strip()
    cat_sel = request.args.get('cat', '')
    forma_sel = request.args.get('forma', '')

    extrato = tx_mes
    if cat_sel:
        extrato = [t for t in extrato if t["categoria"] == cat_sel]
    if forma_sel in ('CREDITO', 'DEBITO'):
        extrato = [
            t for t in extrato
            if t["tipo"] == "DESPESA"
            and (t["forma"] or "DEBITO") == forma_sel
        ]
    if q:
        termo = q.lower()
        extrato = [
            t for t in extrato
            if termo in (t["descricao"] or "").lower()
            or termo in (t["categoria"] or "").lower()
            or termo in (t["detalhes"] or "").lower()
        ]

    return render_template(
        'index.html',
        transacoes=extrato,
        saldo=saldo,
        total_receitas=total_receitas,
        total_despesas=total_despesas,
        total_a_pagar=total_a_pagar,
        total_pago=total_pago,
        pct_pago=pct_pago,
        resumo=resumo,
        orcamentos=orcamentos_status,
        meses=meses,
        categorias=categorias,
        mes_sel=mes_sel,
        q=q,
        cat_sel=cat_sel,
        forma_sel=forma_sel,
        hoje=date.today().isoformat(),
    )


@app.route('/adicionar', methods=['POST'])
@login_required
def adicionar():
    descricao = request.form['descricao']
    valor = float(request.form['valor'])
    tipo = request.form['tipo']
    categoria = request.form['categoria']
    data_lanc = request.form.get('data') or date.today().isoformat()
    # Forma de pagamento só faz sentido para gastos; ganhos ficam NULL.
    forma = request.form.get('forma') if tipo == 'DESPESA' else None
    detalhes = request.form.get('detalhes', '').strip() or None

    conn = get_db()
    conn.execute(
        "INSERT INTO transacoes (descricao, valor, tipo, categoria, data, forma, detalhes, usuario_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (descricao, valor, tipo, categoria, data_lanc, forma, detalhes, session['usuario_id']),
    )
    conn.commit()
    conn.close()

    return redirect('/')


@app.route('/excluir/<int:id>', methods=['POST'])
@login_required
def excluir(id):
    conn = get_db()
    # O filtro por usuario_id impede excluir transação de outra pessoa.
    conn.execute(
        "DELETE FROM transacoes WHERE id = ? AND usuario_id = ?",
        (id, session['usuario_id']),
    )
    conn.commit()
    conn.close()
    return redirect('/')


@app.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar(id):
    conn = get_db()
    uid = session['usuario_id']

    if request.method == 'POST':
        tipo = request.form['tipo']
        forma = request.form.get('forma') if tipo == 'DESPESA' else None
        detalhes = request.form.get('detalhes', '').strip() or None
        conn.execute(
            "UPDATE transacoes SET descricao = ?, valor = ?, tipo = ?, "
            "categoria = ?, data = ?, forma = ?, detalhes = ? WHERE id = ? AND usuario_id = ?",
            (
                request.form['descricao'],
                float(request.form['valor']),
                tipo,
                request.form['categoria'],
                request.form.get('data') or date.today().isoformat(),
                forma,
                detalhes,
                id,
                uid,
            ),
        )
        conn.commit()
        conn.close()
        return redirect('/')

    transacao = conn.execute(
        "SELECT * FROM transacoes WHERE id = ? AND usuario_id = ?", (id, uid)
    ).fetchone()
    cats = conn.execute(
        "SELECT DISTINCT categoria FROM transacoes "
        "WHERE usuario_id = ? AND categoria <> '' ORDER BY categoria COLLATE NOCASE",
        (uid,),
    ).fetchall()
    conn.close()

    if transacao is None:
        return redirect('/')

    categorias = [c["categoria"] for c in cats]
    return render_template('editar.html', t=transacao, categorias=categorias)


@app.route('/orcamentos', methods=['GET', 'POST'])
@login_required
def orcamentos():
    uid = session['usuario_id']
    conn = get_db()

    if request.method == 'POST':
        categoria = request.form.get('categoria', '').strip()
        try:
            limite = float(request.form.get('limite', '0'))
        except ValueError:
            limite = 0
        if categoria and limite > 0:
            # Upsert: um limite por categoria do usuário.
            conn.execute(
                "INSERT INTO orcamentos (usuario_id, categoria, limite) VALUES (?, ?, ?) "
                "ON CONFLICT (usuario_id, categoria) DO UPDATE SET limite = excluded.limite",
                (uid, categoria, limite),
            )
            conn.commit()
        else:
            flash("Informe uma categoria e um limite maior que zero.", "erro")
        conn.close()
        return redirect(url_for('orcamentos'))

    lista = conn.execute(
        "SELECT * FROM orcamentos WHERE usuario_id = ? ORDER BY categoria COLLATE NOCASE",
        (uid,),
    ).fetchall()
    cats = conn.execute(
        "SELECT DISTINCT categoria FROM transacoes "
        "WHERE usuario_id = ? AND categoria <> '' ORDER BY categoria COLLATE NOCASE",
        (uid,),
    ).fetchall()
    conn.close()

    return render_template(
        'orcamentos.html',
        orcamentos=lista,
        categorias=[c["categoria"] for c in cats],
    )


@app.route('/orcamentos/excluir/<int:id>', methods=['POST'])
@login_required
def excluir_orcamento(id):
    conn = get_db()
    conn.execute(
        "DELETE FROM orcamentos WHERE id = ? AND usuario_id = ?",
        (id, session['usuario_id']),
    )
    conn.commit()
    conn.close()
    return redirect(url_for('orcamentos'))


# Garante que as tabelas existam assim que o módulo é importado (produção/WSGI).
init_db()


if __name__ == '__main__':
    app.run(debug=True)
