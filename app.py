import calendar
import csv
import io
import json
import math
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import (
    Flask, render_template, request, redirect, session, flash, url_for,
    Response, abort
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


def voltar_para(padrao='/'):
    """Devolve o Referer só se for do próprio site (evita open-redirect)."""
    ref = request.referrer
    if ref and (not urlparse(ref).netloc or urlparse(ref).netloc == urlparse(request.host_url).netloc):
        return ref
    return padrao


@app.before_request
def csrf_protect():
    """Gera um token de sessão e valida em todo POST (proteção CSRF)."""
    if 'csrf' not in session:
        session['csrf'] = secrets.token_hex(16)
    if request.method == 'POST':
        if request.form.get('csrf', '') != session['csrf']:
            abort(400)


@app.context_processor
def inject_globais():
    """Expõe o token CSRF e o status de admin (sempre fresco do banco) aos templates."""
    eh_admin = bool('usuario_id' in session and is_admin(session['usuario_id']))
    return {'csrf': session.get('csrf', ''), 'eh_admin': eh_admin}


def parse_valor(texto):
    """Converte um valor digitado em float, aceitando o formato brasileiro.

    Ex.: '50,00'->50.0 | '1.234,56'->1234.56 | '1.000'->1000.0 | '1.000.000'->1e6
    Negativos são permitidos (ajuste/estorno). Rejeita vazio, texto e inf/nan.
    """
    s = (texto or '').strip()
    if not s:
        raise ValueError('valor vazio')
    if ',' in s:
        # vírgula = decimal; ponto = separador de milhar (padrão BR)
        s = s.replace('.', '').replace(',', '.')
    elif s.count('.') > 1:
        # vários pontos sem vírgula = só separadores de milhar (1.000.000)
        s = s.replace('.', '')
    elif '.' in s:
        # um ponto só é ambíguo: '1.000' (milhar) vs '50.00'/'1.5' (decimal).
        # 3 dígitos depois do ponto => milhar; senão, decimal.
        inteiro, frac = s.rsplit('.', 1)
        if len(frac) == 3 and inteiro.lstrip('+-').isdigit():
            s = inteiro + frac
    v = float(s)            # lança ValueError em texto inválido
    if not math.isfinite(v):
        raise ValueError('valor não finito')
    return v


def add_months(iso, n):
    """Soma n meses a uma data 'AAAA-MM-DD', ajustando o dia ao fim do mês."""
    d = datetime.strptime(iso, "%Y-%m-%d").date()
    total = d.month - 1 + n
    ano = d.year + total // 12
    mes = total % 12 + 1
    dia = min(d.day, calendar.monthrange(ano, mes)[1])
    return date(ano, mes, dia).isoformat()


def registrar_categoria(conn, uid, nome):
    """Cadastra a categoria na lista do usuário se ainda não existir.

    Permite "criar" categoria só digitando num formulário (lançamento, orçamento,
    recorrente) — sem precisar abrir a página de Categorias. O UNIQUE COLLATE
    NOCASE evita duplicar por maiúsc/minúsc; INSERT OR IGNORE ignora repetidas.
    Não faz commit (quem chama decide).
    """
    nome = (nome or '').strip()
    if nome:
        conn.execute(
            "INSERT OR IGNORE INTO categorias (usuario_id, nome) VALUES (?, ?)",
            (uid, nome),
        )


def categorias_do_usuario(conn, uid):
    """Lista de categorias para sugestão: as gerenciadas + as já usadas em
    lançamentos, sem repetir (ignora maiúsc/minúsc) e em ordem alfabética."""
    nomes = {}  # chave lower -> nome de exibição
    for r in conn.execute(
        "SELECT nome FROM categorias WHERE usuario_id = ? ORDER BY nome COLLATE NOCASE",
        (uid,),
    ):
        nomes.setdefault(r["nome"].strip().lower(), r["nome"].strip())
    for r in conn.execute(
        "SELECT DISTINCT categoria FROM transacoes WHERE usuario_id = ? AND categoria <> ''",
        (uid,),
    ):
        nomes.setdefault(r["categoria"].strip().lower(), r["categoria"].strip())
    return sorted(nomes.values(), key=str.lower)


def eh_ultimo_admin(conn, uid):
    """True se o usuário é admin e é o único admin (não pode perder o status)."""
    alvo = conn.execute("SELECT is_admin FROM users WHERE id = ?", (uid,)).fetchone()
    if not alvo or not alvo["is_admin"]:
        return False
    return conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0] <= 1


def registrar_auditoria(conn, acao, detalhe):
    """Registra uma ação administrativa (quem fez, o quê, quando). Sem commit."""
    conn.execute(
        "INSERT INTO auditoria (admin_id, admin_nome, acao, detalhe, quando) "
        "VALUES (?, ?, ?, ?, ?)",
        (session.get('usuario_id'), session.get('usuario_nome'), acao, detalhe,
         datetime.now().strftime('%Y-%m-%d %H:%M')),
    )


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
    if "pago" not in colunas:
        # Para gastos no CRÉDITO: 1 = fatura já paga; 0 = ainda a pagar.
        conn.execute("ALTER TABLE transacoes ADD COLUMN pago INTEGER NOT NULL DEFAULT 0")
    if "grupo" not in colunas:
        # Identifica as parcelas de uma mesma compra (mesmo grupo). NULL = avulso.
        conn.execute("ALTER TABLE transacoes ADD COLUMN grupo TEXT")

    # Admin: marca o usuário com privilégio de administração.
    ucols = [c["name"] for c in conn.execute("PRAGMA table_info(users)")]
    if "is_admin" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    if "falhas" not in ucols:
        # Controle anti-força-bruta: tentativas de login falhas e bloqueio temporário.
        conn.execute("ALTER TABLE users ADD COLUMN falhas INTEGER NOT NULL DEFAULT 0")
    if "bloqueio_ate" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN bloqueio_ate TEXT")
    # Garante ao menos um admin: promove o usuário mais antigo se não houver nenhum.
    tem_admin = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if tem_admin == 0 and total_users > 0:
        primeiro = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()[0]
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (primeiro,))

    # Categorias gerenciadas pelo usuário. COLLATE NOCASE no nome faz o UNIQUE
    # tratar "Mercado" e "mercado" como a mesma categoria (sem duplicar).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS categorias (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            nome       TEXT    NOT NULL COLLATE NOCASE,
            UNIQUE (usuario_id, nome)
        )
    """)

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

    # Auditoria: registro simples das ações administrativas (quem, o quê, quando).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auditoria (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id   INTEGER,
            admin_nome TEXT,
            acao       TEXT NOT NULL,
            detalhe    TEXT,
            quando     TEXT NOT NULL
        )
    """)

    # Recorrentes: modelos que geram uma transação por mês (salário, aluguel...).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recorrentes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id  INTEGER NOT NULL,
            descricao   TEXT    NOT NULL,
            valor       REAL    NOT NULL,
            tipo        TEXT    NOT NULL,
            categoria   TEXT    NOT NULL,
            forma       TEXT,
            detalhes    TEXT,
            dia         INTEGER NOT NULL,
            ultimo_mes_gerado TEXT
        )
    """)

    conn.commit()
    conn.close()


def gerar_recorrentes(conn, uid):
    """Cria a transação do mês atual para cada recorrente cujo dia já chegou.

    Usa a mesma conexão da chamada. Não faz commit (quem chama decide).
    Comparação 'AAAA-MM' funciona como ordem cronológica (lexicográfica).
    Só gera a partir do dia configurado (não lança valor futuro) e usa um
    "claim" atômico (UPDATE condicional + rowcount) para evitar duplicar
    o lançamento quando dois acessos simultâneos rodam ao mesmo tempo.
    """
    hoje = date.today()
    mes_atual = hoje.strftime("%Y-%m")
    pendentes = conn.execute(
        "SELECT * FROM recorrentes WHERE usuario_id = ? "
        "AND (ultimo_mes_gerado IS NULL OR ultimo_mes_gerado < ?)",
        (uid, mes_atual),
    ).fetchall()
    gerados = 0
    for r in pendentes:
        dia = min(r["dia"], calendar.monthrange(hoje.year, hoje.month)[1])
        # Só gera quando o dia do mês já chegou — não conta valor futuro.
        if hoje.day < dia:
            continue
        # Claim atômico: marca como gerado só se ninguém marcou ainda este mês.
        # Se outro request já gerou (rowcount 0), não duplica o lançamento.
        claimed = conn.execute(
            "UPDATE recorrentes SET ultimo_mes_gerado = ? "
            "WHERE id = ? AND (ultimo_mes_gerado IS NULL OR ultimo_mes_gerado < ?)",
            (mes_atual, r["id"], mes_atual),
        ).rowcount
        if not claimed:
            continue
        data_lanc = date(hoje.year, hoje.month, dia).isoformat()
        forma = r["forma"] if r["tipo"] == "DESPESA" else None
        conn.execute(
            "INSERT INTO transacoes (descricao, valor, tipo, categoria, data, forma, detalhes, usuario_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (r["descricao"], r["valor"], r["tipo"], r["categoria"],
             data_lanc, forma, r["detalhes"], uid),
        )
        gerados += 1
    return gerados


# ---- Autenticação ----

def login_required(f):
    """Protege rotas: sem sessão, manda pro login."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def is_admin(uid):
    """Consulta no banco se o usuário é admin (fonte da verdade, não a sessão)."""
    conn = get_db()
    u = conn.execute("SELECT is_admin FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    return bool(u and u["is_admin"])


def admin_required(f):
    """Protege rotas de administração: exige sessão E privilégio de admin."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("login"))
        if not is_admin(session["usuario_id"]):
            return redirect('/')
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
        # COLLATE NOCASE: 'Gustavo' e 'gustavo' são o mesmo usuário (evita duplicar).
        existe = conn.execute(
            "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (username,)
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

        # Se for o primeiro usuário: adota transações sem dono e vira admin.
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        eh_primeiro = total_users == 1
        if eh_primeiro:
            conn.execute(
                "UPDATE transacoes SET usuario_id = ? WHERE usuario_id IS NULL",
                (novo_id,),
            )
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (novo_id,))

        conn.commit()
        conn.close()

        session['usuario_id'] = novo_id
        session['usuario_nome'] = username
        session['is_admin'] = 1 if eh_primeiro else 0
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
        MAX_FALHAS, BLOQUEIO_MIN = 5, 5
        FMT = '%Y-%m-%d %H:%M:%S'
        agora = datetime.now()

        conn = get_db()
        # COLLATE NOCASE: aceita o nome em qualquer caixa (não tranca o usuário fora).
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()

        # Bloqueio temporário após muitas tentativas (anti-força-bruta).
        falhas_base = (user["falhas"] or 0) if user else 0
        if user and user["bloqueio_ate"]:
            try:
                ate = datetime.strptime(user["bloqueio_ate"], FMT)
            except (ValueError, TypeError):
                ate = None
            if ate and ate > agora:
                conn.close()
                flash("Muitas tentativas. Tente novamente em alguns minutos.", "erro")
                return render_template('login.html', username=username)
            if ate:
                # Bloqueio expirou: recomeça a contagem (5 novas tentativas).
                falhas_base = 0
                conn.execute("UPDATE users SET falhas = 0, bloqueio_ate = NULL WHERE id = ?", (user["id"],))

        if user is None or not check_password_hash(user["senha_hash"], senha):
            if user:
                falhas = falhas_base + 1
                if falhas >= MAX_FALHAS:
                    bloq = (agora + timedelta(minutes=BLOQUEIO_MIN)).strftime(FMT)
                    conn.execute("UPDATE users SET falhas = ?, bloqueio_ate = ? WHERE id = ?",
                                 (falhas, bloq, user["id"]))
                else:
                    conn.execute("UPDATE users SET falhas = ? WHERE id = ?", (falhas, user["id"]))
                conn.commit()
            conn.close()
            flash("Usuário ou senha inválidos.", "erro")
            return render_template('login.html', username=username)

        # Sucesso: zera o contador de tentativas.
        conn.execute("UPDATE users SET falhas = 0, bloqueio_ate = NULL WHERE id = ?", (user["id"],))
        conn.commit()
        conn.close()

        session['usuario_id'] = user["id"]
        session['usuario_nome'] = user["username"]
        session['is_admin'] = user["is_admin"]
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
    # Padrão = mês atual (orçamento e fatura são conceitos mensais). 'todos'
    # continua disponível no filtro para a visão de longo prazo.
    mes_atual = date.today().strftime("%Y-%m")
    mes_sel = request.args.get('mes', mes_atual)
    uid = session['usuario_id']

    conn = get_db()
    # Gera os lançamentos recorrentes do mês atual antes de ler as transações.
    # Só faz commit (escrita) se algo foi realmente gerado — evita travar a
    # leitura da home com uma transação de escrita à toa a cada acesso.
    if gerar_recorrentes(conn, uid):
        conn.commit()
    todas = conn.execute(
        "SELECT * FROM transacoes WHERE usuario_id = ? ORDER BY data DESC, id DESC",
        (uid,),
    ).fetchall()
    orcamentos_db = conn.execute(
        "SELECT categoria, limite FROM orcamentos WHERE usuario_id = ?",
        (uid,),
    ).fetchall()
    # Sugestões de categoria: gerenciadas + já usadas (sem repetir por caixa).
    categorias = categorias_do_usuario(conn, uid)
    conn.close()

    # Meses disponíveis para o filtro (do mais recente ao mais antigo).
    # Inclui sempre o mês atual, mesmo que ainda não haja lançamentos nele.
    meses = sorted(
        {t["data"][:7] for t in todas if t["data"]} | {mes_atual},
        reverse=True,
    )

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
        if t["tipo"] == "DESPESA" and t["forma"] == "CREDITO" and not t["pago"]
    )
    total_pago = total_despesas - total_a_pagar
    pct_pago = (total_pago / total_despesas * 100) if total_despesas else 0

    # Gastos por categoria (só DESPESA), agrupados SEM diferenciar maiúsc/minúsc
    # nem espaços — assim "Mercado", "mercado" e "Mercado " contam como uma só.
    gasto_por_chave = {}   # categoria normalizada (lower) -> total
    nome_por_chave = {}    # categoria normalizada -> nome de exibição (1º visto)
    for t in tx_mes:
        if t["tipo"] == "DESPESA":
            cat = (t["categoria"] or "").strip()
            chave = cat.lower()
            gasto_por_chave[chave] = gasto_por_chave.get(chave, 0) + t["valor"]
            nome_por_chave.setdefault(chave, cat)

    maior = max(gasto_por_chave.values()) if gasto_por_chave else 0
    resumo = [
        {
            "categoria": nome_por_chave[chave],
            "total": total,
            "pct_barra": (total / maior * 100) if maior else 0,
            "pct_total": (total / total_despesas * 100) if total_despesas else 0,
        }
        for chave, total in sorted(gasto_por_chave.items(), key=lambda x: x[1], reverse=True)
    ]

    # Orçamentos: compara o gasto do mês na categoria com o limite (case-insensitive).
    orcamentos_status = []
    for o in orcamentos_db:
        gasto = gasto_por_chave.get((o["categoria"] or "").strip().lower(), 0)
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

    # Insights: frases automáticas sobre o mês selecionado (só quando há gastos
    # e um mês específico está selecionado — em "todos" não faria sentido comparar).
    insights = []
    if mes_sel != 'todos' and total_despesas > 0:
        try:
            ano_sel, num_mes = (int(x) for x in mes_sel.split('-'))
        except (ValueError, TypeError):
            ano_sel = num_mes = None

        if ano_sel:
            # Comparação com o mês anterior.
            mes_ant = (date(ano_sel, num_mes, 1) - timedelta(days=1)).strftime("%Y-%m")
            gasto_ant = sum(
                t["valor"] for t in todas
                if t["tipo"] == "DESPESA" and t["data"] and t["data"].startswith(mes_ant)
            )
            if gasto_ant > 0:
                diff = (total_despesas - gasto_ant) / gasto_ant * 100
                if diff >= 1:
                    insights.append(f"📈 Você gastou {diff:.0f}% a mais que no mês anterior.")
                elif diff <= -1:
                    insights.append(f"📉 Boa! Você gastou {abs(diff):.0f}% a menos que no mês anterior.")
                else:
                    insights.append("➖ Seus gastos ficaram parecidos com os do mês anterior.")

            # Média diária: até hoje no mês corrente, mês cheio nos passados.
            if mes_sel == mes_atual:
                dias = date.today().day
            else:
                dias = calendar.monthrange(ano_sel, num_mes)[1]
            if dias > 0:
                insights.append(f"💸 Média de R$ {moeda(total_despesas / dias)} por dia em gastos.")

        # Maior categoria do mês.
        if resumo:
            top = resumo[0]
            insights.append(
                f"🏆 Onde mais foi: {top['categoria']} "
                f"(R$ {moeda(top['total'])}, {top['pct_total']:.0f}% dos gastos)."
            )

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
    elif forma_sel == 'A_PAGAR':
        # Só gastos no crédito ainda não pagos.
        extrato = [
            t for t in extrato
            if t["tipo"] == "DESPESA" and t["forma"] == "CREDITO" and not t["pago"]
        ]
    if q:
        termo = q.lower()
        extrato = [
            t for t in extrato
            if termo in (t["descricao"] or "").lower()
            or termo in (t["categoria"] or "").lower()
            or termo in (t["detalhes"] or "").lower()
        ]

    # Paginação do extrato (20 por página).
    POR_PAGINA = 20
    total_itens = len(extrato)
    total_paginas = max(1, (total_itens + POR_PAGINA - 1) // POR_PAGINA)
    pagina = request.args.get('pagina', 1, type=int)
    pagina = max(1, min(pagina, total_paginas))
    extrato_pagina = extrato[(pagina - 1) * POR_PAGINA: pagina * POR_PAGINA]

    return render_template(
        'index.html',
        transacoes=extrato_pagina,
        pagina=pagina,
        total_paginas=total_paginas,
        total_itens=total_itens,
        saldo=saldo,
        total_receitas=total_receitas,
        total_despesas=total_despesas,
        total_a_pagar=total_a_pagar,
        total_pago=total_pago,
        pct_pago=pct_pago,
        resumo=resumo,
        insights=insights,
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
    descricao = request.form.get('descricao', '').strip()
    try:
        valor = parse_valor(request.form.get('valor'))
    except (ValueError, TypeError):
        flash("Valor inválido. Use números (ex.: 50,00).", "erro")
        return redirect('/')
    if valor == 0:
        flash("O valor não pode ser zero.", "erro")
        return redirect('/')
    tipo = request.form.get('tipo', 'DESPESA')
    categoria = request.form.get('categoria', '').strip()
    data_lanc = request.form.get('data') or date.today().isoformat()
    # Forma de pagamento só faz sentido para gastos; ganhos ficam NULL.
    forma = request.form.get('forma') if tipo == 'DESPESA' else None
    detalhes = request.form.get('detalhes', '').strip() or None
    uid = session['usuario_id']

    # Parcelamento: só vale para gasto no crédito.
    parcelas = 1
    if tipo == 'DESPESA' and forma == 'CREDITO':
        try:
            parcelas = int((request.form.get('parcelas', '1') or '1').strip())
        except ValueError:
            flash("Número de parcelas inválido.", "erro")
            return redirect('/')
        if parcelas < 1 or parcelas > 60:
            flash("As parcelas devem ser um número de 1 a 60.", "erro")
            return redirect('/')

    conn = get_db()
    registrar_categoria(conn, uid, categoria)
    if parcelas > 1:
        # Divide o valor em N parcelas; a sobra de centavos vai pra 1ª parcela.
        # Todas compartilham o mesmo 'grupo' (permite excluir a compra inteira).
        grupo = secrets.token_hex(8)
        base = round(valor / parcelas, 2)
        sobra = round(valor - base * parcelas, 2)
        for i in range(parcelas):
            v = round(base + sobra, 2) if i == 0 else base
            conn.execute(
                "INSERT INTO transacoes (descricao, valor, tipo, categoria, data, forma, detalhes, usuario_id, grupo) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"{descricao} ({i + 1}/{parcelas})",
                    v, tipo, categoria, add_months(data_lanc, i), forma, detalhes, uid, grupo,
                ),
            )
    else:
        conn.execute(
            "INSERT INTO transacoes (descricao, valor, tipo, categoria, data, forma, detalhes, usuario_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (descricao, valor, tipo, categoria, data_lanc, forma, detalhes, uid),
        )
    conn.commit()
    conn.close()

    return redirect('/')


@app.route('/pagar/<int:id>', methods=['POST'])
@login_required
def pagar(id):
    # Alterna o status de pago de um gasto no crédito (só do próprio usuário).
    conn = get_db()
    conn.execute(
        "UPDATE transacoes SET pago = CASE WHEN pago = 1 THEN 0 ELSE 1 END "
        "WHERE id = ? AND usuario_id = ? AND forma = 'CREDITO'",
        (id, session['usuario_id']),
    )
    conn.commit()
    conn.close()
    return redirect(voltar_para())


@app.route('/excluir/<int:id>', methods=['POST'])
@login_required
def excluir(id):
    uid = session['usuario_id']
    escopo = request.form.get('escopo', 'esta')
    conn = get_db()
    # O filtro por usuario_id impede excluir transação de outra pessoa.
    if escopo == 'todas':
        # Exclui todas as parcelas da mesma compra (mesmo grupo), se houver.
        alvo = conn.execute(
            "SELECT grupo FROM transacoes WHERE id = ? AND usuario_id = ?", (id, uid)
        ).fetchone()
        if alvo and alvo["grupo"]:
            conn.execute(
                "DELETE FROM transacoes WHERE grupo = ? AND usuario_id = ?",
                (alvo["grupo"], uid),
            )
        else:
            conn.execute("DELETE FROM transacoes WHERE id = ? AND usuario_id = ?", (id, uid))
    else:
        conn.execute("DELETE FROM transacoes WHERE id = ? AND usuario_id = ?", (id, uid))
    conn.commit()
    conn.close()
    return redirect(voltar_para())


@app.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar(id):
    conn = get_db()
    uid = session['usuario_id']

    if request.method == 'POST':
        tipo = request.form.get('tipo', 'DESPESA')
        forma = request.form.get('forma') if tipo == 'DESPESA' else None
        detalhes = request.form.get('detalhes', '').strip() or None
        try:
            valor = parse_valor(request.form.get('valor'))
        except (ValueError, TypeError):
            conn.close()
            flash("Valor inválido. Use números (ex.: 50,00).", "erro")
            return redirect(url_for('editar', id=id))
        if valor == 0:
            conn.close()
            flash("O valor não pode ser zero.", "erro")
            return redirect(url_for('editar', id=id))
        categoria = request.form.get('categoria', '').strip()
        registrar_categoria(conn, uid, categoria)
        conn.execute(
            "UPDATE transacoes SET descricao = ?, valor = ?, tipo = ?, "
            "categoria = ?, data = ?, forma = ?, detalhes = ? WHERE id = ? AND usuario_id = ?",
            (
                request.form.get('descricao', '').strip(),
                valor,
                tipo,
                categoria,
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
    categorias = categorias_do_usuario(conn, uid)
    conn.close()

    if transacao is None:
        return redirect('/')

    return render_template('editar.html', t=transacao, categorias=categorias)


@app.route('/orcamentos', methods=['GET', 'POST'])
@login_required
def orcamentos():
    uid = session['usuario_id']
    conn = get_db()

    if request.method == 'POST':
        categoria = request.form.get('categoria', '').strip()
        try:
            limite = parse_valor(request.form.get('limite', ''))
        except (ValueError, TypeError):
            limite = None
        if not categoria:
            flash("Informe uma categoria.", "erro")
        elif limite is None:
            flash("Limite inválido. Use números (ex.: 600,00).", "erro")
        elif limite <= 0:
            flash("O limite deve ser maior que zero.", "erro")
        else:
            registrar_categoria(conn, uid, categoria)
            # Upsert: um limite por categoria do usuário (edita se já existir).
            conn.execute(
                "INSERT INTO orcamentos (usuario_id, categoria, limite) VALUES (?, ?, ?) "
                "ON CONFLICT (usuario_id, categoria) DO UPDATE SET limite = excluded.limite",
                (uid, categoria, limite),
            )
            conn.commit()
            flash("Orçamento salvo. 🎯", "ok")
        conn.close()
        return redirect(url_for('orcamentos'))

    lista = conn.execute(
        "SELECT * FROM orcamentos WHERE usuario_id = ? ORDER BY categoria COLLATE NOCASE",
        (uid,),
    ).fetchall()
    # Gasto do MÊS ATUAL por categoria (case-insensitive) para a barra de progresso.
    mes_atual = date.today().strftime("%Y-%m")
    gasto_por_chave = {}
    for t in conn.execute(
        "SELECT categoria, valor FROM transacoes "
        "WHERE usuario_id = ? AND tipo = 'DESPESA' AND data LIKE ?",
        (uid, mes_atual + '%'),
    ):
        chave = (t["categoria"] or "").strip().lower()
        gasto_por_chave[chave] = gasto_por_chave.get(chave, 0) + t["valor"]
    categorias = categorias_do_usuario(conn, uid)
    conn.close()

    orcamentos_lista = []
    for o in lista:
        gasto = gasto_por_chave.get(o["categoria"].strip().lower(), 0)
        limite = o["limite"]
        pct = (gasto / limite * 100) if limite else 0
        orcamentos_lista.append({
            "id": o["id"], "categoria": o["categoria"], "limite": limite,
            "gasto": gasto, "pct": pct, "pct_barra": min(pct, 100),
            "estourou": gasto > limite, "restante": limite - gasto,
        })

    return render_template(
        'orcamentos.html',
        orcamentos=orcamentos_lista,
        categorias=categorias,
        mes_atual=mes_atual,
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


@app.route('/recorrentes', methods=['GET', 'POST'])
@login_required
def recorrentes():
    uid = session['usuario_id']
    conn = get_db()

    if request.method == 'POST':
        descricao = request.form.get('descricao', '').strip()
        categoria = request.form.get('categoria', '').strip()
        tipo = request.form.get('tipo', 'DESPESA')
        forma = request.form.get('forma') if tipo == 'DESPESA' else None
        detalhes = request.form.get('detalhes', '').strip() or None
        # Validação no servidor com mensagens claras.
        try:
            valor = parse_valor(request.form.get('valor', ''))
        except (ValueError, TypeError):
            valor = None
        try:
            dia = int(request.form.get('dia', ''))
        except (ValueError, TypeError):
            dia = None

        erro = None
        if not descricao or not categoria:
            erro = "Preencha descrição e categoria."
        elif valor is None:
            erro = "Valor inválido. Use números (ex.: 50,00)."
        elif valor == 0:
            erro = "O valor não pode ser zero."
        elif dia is None or dia < 1 or dia > 31:
            erro = "O dia do mês deve ser um número de 1 a 31."

        if erro:
            flash(erro, "erro")
        else:
            registrar_categoria(conn, uid, categoria)
            conn.execute(
                "INSERT INTO recorrentes (usuario_id, descricao, valor, tipo, categoria, forma, detalhes, dia) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, descricao, valor, tipo, categoria, forma, detalhes, dia),
            )
            # Gera já o lançamento do mês atual para o novo recorrente.
            gerar_recorrentes(conn, uid)
            conn.commit()
            flash("Recorrente criado! O lançamento deste mês já foi gerado. 🔁", "ok")
        conn.close()
        return redirect(url_for('recorrentes'))

    lista = conn.execute(
        "SELECT * FROM recorrentes WHERE usuario_id = ? ORDER BY tipo, descricao COLLATE NOCASE",
        (uid,),
    ).fetchall()
    categorias = categorias_do_usuario(conn, uid)
    conn.close()

    return render_template(
        'recorrentes.html',
        recorrentes=lista,
        categorias=categorias,
    )


@app.route('/recorrentes/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_recorrente(id):
    uid = session['usuario_id']
    conn = get_db()

    if request.method == 'POST':
        descricao = request.form.get('descricao', '').strip()
        categoria = request.form.get('categoria', '').strip()
        tipo = request.form.get('tipo', 'DESPESA')
        forma = request.form.get('forma') if tipo == 'DESPESA' else None
        detalhes = request.form.get('detalhes', '').strip() or None
        try:
            valor = parse_valor(request.form.get('valor', ''))
        except (ValueError, TypeError):
            valor = None
        try:
            dia = int(request.form.get('dia', ''))
        except (ValueError, TypeError):
            dia = None

        erro = None
        if not descricao or not categoria:
            erro = "Preencha descrição e categoria."
        elif valor is None:
            erro = "Valor inválido. Use números (ex.: 50,00)."
        elif valor == 0:
            erro = "O valor não pode ser zero."
        elif dia is None or dia < 1 or dia > 31:
            erro = "O dia do mês deve ser um número de 1 a 31."

        if erro:
            flash(erro, "erro")
            conn.close()
            return redirect(url_for('editar_recorrente', id=id))
        registrar_categoria(conn, uid, categoria)
        # Edita só o modelo: afeta os PRÓXIMOS meses; os já lançados não mudam.
        conn.execute(
            "UPDATE recorrentes SET descricao = ?, valor = ?, tipo = ?, categoria = ?, "
            "forma = ?, detalhes = ?, dia = ? WHERE id = ? AND usuario_id = ?",
            (descricao, valor, tipo, categoria, forma, detalhes, dia, id, uid),
        )
        conn.commit()
        conn.close()
        flash("Recorrente atualizado. Vale para os próximos meses.", "ok")
        return redirect(url_for('recorrentes'))

    r = conn.execute(
        "SELECT * FROM recorrentes WHERE id = ? AND usuario_id = ?", (id, uid)
    ).fetchone()
    categorias = categorias_do_usuario(conn, uid)
    conn.close()
    if r is None:
        return redirect(url_for('recorrentes'))
    return render_template('recorrente_editar.html', r=r, categorias=categorias)


@app.route('/recorrentes/excluir/<int:id>', methods=['POST'])
@login_required
def excluir_recorrente(id):
    conn = get_db()
    conn.execute(
        "DELETE FROM recorrentes WHERE id = ? AND usuario_id = ?",
        (id, session['usuario_id']),
    )
    conn.commit()
    conn.close()
    return redirect(url_for('recorrentes'))


# ---- Categorias: gerenciar a lista de categorias ----

@app.route('/categorias', methods=['GET', 'POST'])
@login_required
def categorias():
    uid = session['usuario_id']
    conn = get_db()

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        if not nome:
            flash("Digite um nome de categoria.", "erro")
        else:
            try:
                conn.execute(
                    "INSERT INTO categorias (usuario_id, nome) VALUES (?, ?)",
                    (uid, nome),
                )
                conn.commit()
                flash("Categoria criada. 🏷️", "ok")
            except sqlite3.IntegrityError:
                flash("Você já tem uma categoria com esse nome.", "erro")
        conn.close()
        return redirect(url_for('categorias'))

    lista = conn.execute(
        "SELECT * FROM categorias WHERE usuario_id = ? ORDER BY nome COLLATE NOCASE",
        (uid,),
    ).fetchall()
    # Quantos lançamentos usam cada categoria (case-insensitive), para informar.
    usos = {}
    for r in conn.execute(
        "SELECT categoria, COUNT(*) AS n FROM transacoes "
        "WHERE usuario_id = ? AND categoria <> '' GROUP BY categoria COLLATE NOCASE",
        (uid,),
    ):
        usos[r["categoria"].strip().lower()] = usos.get(r["categoria"].strip().lower(), 0) + r["n"]
    conn.close()

    return render_template('categorias.html', categorias=lista, usos=usos)


@app.route('/categorias/excluir/<int:id>', methods=['POST'])
@login_required
def excluir_categoria(id):
    # Remove só o item da lista gerenciada; os lançamentos existentes não mudam.
    conn = get_db()
    conn.execute(
        "DELETE FROM categorias WHERE id = ? AND usuario_id = ?",
        (id, session['usuario_id']),
    )
    conn.commit()
    conn.close()
    return redirect(url_for('categorias'))


# ---- Exportar transações em CSV (backup / análise no Excel) ----

@app.route('/exportar')
@login_required
def exportar():
    uid = session['usuario_id']
    conn = get_db()
    rows = conn.execute(
        "SELECT data, descricao, categoria, tipo, forma, valor, detalhes "
        "FROM transacoes WHERE usuario_id = ? ORDER BY data, id",
        (uid,),
    ).fetchall()
    conn.close()

    buf = io.StringIO()
    buf.write('﻿')  # BOM: garante acentos corretos no Excel
    w = csv.writer(buf, delimiter=';')   # ';' = padrão pt-BR do Excel
    w.writerow(['Data', 'Descrição', 'Categoria', 'Tipo', 'Forma', 'Valor', 'Detalhes'])
    for r in rows:
        tipo = 'Ganho' if r["tipo"] == 'RECEITA' else 'Gasto'
        forma = '' if not r["forma"] else ('Crédito' if r["forma"] == 'CREDITO' else 'Débito')
        valor = f"{r['valor']:.2f}".replace('.', ',')   # decimal com vírgula
        w.writerow([data_br(r["data"]), r["descricao"], r["categoria"],
                    tipo, forma, valor, r["detalhes"] or ''])

    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=wheresmycash.csv'},
    )


# ---- Conta: backup e restauração dos dados do usuário ----

# Tabelas e colunas incluídas no backup (só dados do próprio usuário).
BACKUP_TABELAS = {
    "transacoes": ["descricao", "valor", "tipo", "categoria", "data",
                   "forma", "detalhes", "pago", "grupo"],
    "categorias": ["nome"],
    "orcamentos": ["categoria", "limite"],
    "recorrentes": ["descricao", "valor", "tipo", "categoria", "forma",
                    "detalhes", "dia", "ultimo_mes_gerado"],
}


@app.route('/conta')
@login_required
def conta():
    return render_template('conta.html')


@app.route('/backup')
@login_required
def backup():
    """Baixa um arquivo JSON com TODOS os dados do próprio usuário."""
    uid = session['usuario_id']
    conn = get_db()
    dados = {"versao": 1, "gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M")}
    for tabela, cols in BACKUP_TABELAS.items():
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {tabela} WHERE usuario_id = ?", (uid,)
        ).fetchall()
        dados[tabela] = [dict(r) for r in rows]
    conn.close()

    corpo = json.dumps(dados, ensure_ascii=False, indent=2)
    nome = "wheresmycash-backup-" + date.today().isoformat() + ".json"
    return Response(
        corpo,
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename={nome}'},
    )


@app.route('/restaurar', methods=['POST'])
@login_required
def restaurar():
    """Substitui TODOS os dados do usuário pelos do arquivo de backup enviado."""
    uid = session['usuario_id']
    arquivo = request.files.get('arquivo')
    if not arquivo or not arquivo.filename:
        flash("Escolha um arquivo de backup (.json) para restaurar.", "erro")
        return redirect(url_for('conta'))

    try:
        dados = json.loads(arquivo.read().decode('utf-8'))
        assert isinstance(dados, dict)
        assert all(isinstance(dados.get(t, []), list) for t in BACKUP_TABELAS)
    except (ValueError, AssertionError, UnicodeDecodeError):
        flash("Arquivo inválido. Use um backup gerado aqui mesmo.", "erro")
        return redirect(url_for('conta'))

    conn = get_db()
    try:
        # Substitui: apaga o que é do usuário e regrava a partir do backup.
        for tabela in BACKUP_TABELAS:
            conn.execute(f"DELETE FROM {tabela} WHERE usuario_id = ?", (uid,))
        total = 0
        for tabela, cols in BACKUP_TABELAS.items():
            campos = cols + ["usuario_id"]
            ph = ", ".join("?" for _ in campos)
            sql = f"INSERT INTO {tabela} ({', '.join(campos)}) VALUES ({ph})"
            for item in dados.get(tabela, []):
                if not isinstance(item, dict):
                    continue
                valores = [item.get(c) for c in cols] + [uid]
                conn.execute(sql, valores)
                total += 1
        conn.commit()
    except (sqlite3.Error, TypeError):
        conn.rollback()
        conn.close()
        flash("Não consegui restaurar: o arquivo parece corrompido.", "erro")
        return redirect(url_for('conta'))
    conn.close()
    flash(f"Backup restaurado! {total} registro(s) recuperados. ✅", "ok")
    return redirect('/')


# ---- PWA: service worker (servido na raiz para ter escopo '/') ----

@app.route('/sw.js')
def service_worker():
    # Cache-first para os estáticos (fontes/ícones): ficam disponíveis mesmo
    # com rede instável e sem depender de CDN externo.
    js = (
        "const C='wmc-static-v1';\n"
        "self.addEventListener('install', e => self.skipWaiting());\n"
        "self.addEventListener('activate', e => self.clients.claim());\n"
        "self.addEventListener('fetch', e => {\n"
        "  const u = new URL(e.request.url);\n"
        "  if (e.request.method === 'GET' && u.origin === location.origin && u.pathname.startsWith('/static/')) {\n"
        "    e.respondWith(caches.open(C).then(c => c.match(e.request).then(r =>\n"
        "      r || fetch(e.request).then(resp => { if (resp.ok) c.put(e.request, resp.clone()); return resp; }))));\n"
        "  }\n"
        "});\n"
    )
    return Response(js, mimetype='application/javascript')


# ---- Evolução: comparativo mês a mês ----

@app.route('/evolucao')
@login_required
def evolucao():
    uid = session['usuario_id']
    conn = get_db()
    rows = conn.execute(
        "SELECT data, tipo, valor FROM transacoes WHERE usuario_id = ?", (uid,)
    ).fetchall()
    conn.close()

    # Agrega ganhos e gastos por mês (AAAA-MM).
    por_mes = {}
    for r in rows:
        if not r["data"]:
            continue
        m = r["data"][:7]
        d = por_mes.setdefault(m, {"ganhos": 0.0, "gastos": 0.0})
        if r["tipo"] == "RECEITA":
            d["ganhos"] += r["valor"]
        else:
            d["gastos"] += r["valor"]

    # Ordem cronológica, no máximo os 12 meses mais recentes.
    ordenados = sorted(por_mes.items())[-12:]
    maior = max((max(d["ganhos"], d["gastos"]) for _, d in ordenados), default=0)
    dados = [
        {
            "mes": m,
            "ganhos": d["ganhos"],
            "gastos": d["gastos"],
            "saldo": d["ganhos"] - d["gastos"],
            "pg": (d["ganhos"] / maior * 100) if maior else 0,
            "pd": (d["gastos"] / maior * 100) if maior else 0,
        }
        for m, d in ordenados
    ]
    return render_template('evolucao.html', dados=dados)


@app.route('/fatura')
@login_required
def fatura():
    """Quanto cai na fatura de cada mês: gastos no CRÉDITO agrupados por mês,
    incluindo as parcelas que caem lá na frente."""
    uid = session['usuario_id']
    conn = get_db()
    rows = conn.execute(
        "SELECT data, valor, pago, descricao, categoria FROM transacoes "
        "WHERE usuario_id = ? AND tipo = 'DESPESA' AND forma = 'CREDITO' AND data IS NOT NULL",
        (uid,),
    ).fetchall()
    conn.close()

    mes_atual = date.today().strftime("%Y-%m")
    por_mes = {}
    for r in rows:
        m = r["data"][:7]
        d = por_mes.setdefault(m, {"total": 0.0, "pago": 0.0, "aberto": 0.0, "qtd": 0})
        d["total"] += r["valor"]
        d["qtd"] += 1
        if r["pago"]:
            d["pago"] += r["valor"]
        else:
            d["aberto"] += r["valor"]

    # Mostra do mês atual em diante (o que ainda vai pesar no bolso) + meses
    # passados que ainda tenham fatura em aberto (atrasados).
    meses = []
    for m, d in sorted(por_mes.items()):
        if m >= mes_atual or d["aberto"] > 0.005:
            meses.append({
                "mes": m,
                "total": d["total"],
                "pago": d["pago"],
                "aberto": d["aberto"],
                "qtd": d["qtd"],
                "atrasado": m < mes_atual and d["aberto"] > 0.005,
                "atual": m == mes_atual,
                "pct_pago": (d["pago"] / d["total"] * 100) if d["total"] else 0,
            })

    total_aberto = sum(x["aberto"] for x in meses)
    return render_template('fatura.html', meses=meses, total_aberto=total_aberto)


# ---- Trocar a própria senha (qualquer usuário logado) ----

@app.route('/trocar-senha', methods=['GET', 'POST'])
@login_required
def trocar_senha():
    if request.method == 'POST':
        atual = request.form.get('atual', '')
        nova = request.form.get('nova', '')
        nova2 = request.form.get('nova2', '')

        conn = get_db()
        u = conn.execute(
            "SELECT senha_hash FROM users WHERE id = ?", (session['usuario_id'],)
        ).fetchone()

        if not u or not check_password_hash(u["senha_hash"], atual):
            flash("Senha atual incorreta.", "erro")
        elif len(nova) < 4:
            flash("A nova senha precisa ter pelo menos 4 caracteres.", "erro")
        elif nova != nova2:
            flash("A confirmação da nova senha não confere.", "erro")
        else:
            conn.execute(
                "UPDATE users SET senha_hash = ? WHERE id = ?",
                (generate_password_hash(nova), session['usuario_id']),
            )
            conn.commit()
            conn.close()
            flash("Senha alterada com sucesso! 🔑", "ok")
            return redirect('/')
        conn.close()

    return render_template('trocar_senha.html')


# ---- Painel de administração (somente admin) ----

@app.route('/admin')
@admin_required
def admin():
    conn = get_db()
    usuarios = conn.execute("""
        SELECT u.id, u.username, u.criado_em, u.is_admin,
               (SELECT COUNT(*) FROM transacoes t WHERE t.usuario_id = u.id) AS qtd
        FROM users u
        ORDER BY u.username COLLATE NOCASE
    """).fetchall()
    auditoria = conn.execute(
        "SELECT * FROM auditoria ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return render_template('admin.html', usuarios=usuarios, auditoria=auditoria,
                           eu=session['usuario_id'])


@app.route('/admin/reset-senha/<int:id>', methods=['POST'])
@admin_required
def admin_reset_senha(id):
    nova = request.form.get('senha', '')
    if len(nova) < 4:
        flash("A nova senha precisa ter pelo menos 4 caracteres.", "erro")
        return redirect(url_for('admin'))
    conn = get_db()
    alvo = conn.execute("SELECT username FROM users WHERE id = ?", (id,)).fetchone()
    # Redefine a senha e também limpa o bloqueio por tentativas (desbloqueia).
    conn.execute(
        "UPDATE users SET senha_hash = ?, falhas = 0, bloqueio_ate = NULL WHERE id = ?",
        (generate_password_hash(nova), id),
    )
    registrar_auditoria(conn, "Resetou senha", "usuário " + (alvo["username"] if alvo else str(id)))
    conn.commit()
    conn.close()
    flash("Senha redefinida.", "ok")
    return redirect(url_for('admin'))


@app.route('/admin/excluir/<int:id>', methods=['POST'])
@admin_required
def admin_excluir(id):
    if id == session['usuario_id']:
        flash("Você não pode excluir a própria conta pelo painel.", "erro")
        return redirect(url_for('admin'))

    conn = get_db()
    if eh_ultimo_admin(conn, id):
        conn.close()
        flash("Não dá pra excluir o último admin.", "erro")
        return redirect(url_for('admin'))

    alvo = conn.execute("SELECT username FROM users WHERE id = ?", (id,)).fetchone()
    # Remove o usuário e tudo que pertence a ele.
    conn.execute("DELETE FROM transacoes WHERE usuario_id = ?", (id,))
    conn.execute("DELETE FROM orcamentos WHERE usuario_id = ?", (id,))
    conn.execute("DELETE FROM recorrentes WHERE usuario_id = ?", (id,))
    conn.execute("DELETE FROM categorias WHERE usuario_id = ?", (id,))
    conn.execute("DELETE FROM users WHERE id = ?", (id,))
    registrar_auditoria(conn, "Excluiu usuário", alvo["username"] if alvo else str(id))
    conn.commit()
    conn.close()
    flash("Usuário excluído.", "ok")
    return redirect(url_for('admin'))


@app.route('/admin/promover/<int:id>', methods=['POST'])
@admin_required
def admin_promover(id):
    conn = get_db()
    alvo = conn.execute("SELECT username, is_admin FROM users WHERE id = ?", (id,)).fetchone()
    if alvo:
        novo = 0 if alvo["is_admin"] else 1
        if novo == 0 and eh_ultimo_admin(conn, id):
            conn.close()
            flash("Não dá pra remover o último admin.", "erro")
            return redirect(url_for('admin'))
        conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (novo, id))
        registrar_auditoria(conn, "Tornou admin" if novo else "Removeu admin", alvo["username"])
        conn.commit()
        if id == session['usuario_id']:
            session['is_admin'] = novo
    conn.close()
    return redirect(url_for('admin'))


# Garante que as tabelas existam assim que o módulo é importado (produção/WSGI).
init_db()


if __name__ == '__main__':
    app.run(debug=True)
