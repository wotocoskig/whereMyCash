from flask import Flask, render_template, request, redirect

app = Flask(__name__)

# Nossa simulação agora tem "categoria"
transacoes = [
    {"descricao": "Salário", "valor": 3000.00, "tipo": "RECEITA", "categoria": "Renda"},
    {"descricao": "Mercado", "valor": 500.00, "tipo": "DESPESA", "categoria": "Alimentação"},
    {"descricao": "Ifood", "valor": 80.00, "tipo": "DESPESA", "categoria": "Alimentação"},
    {"descricao": "Cinema", "valor": 60.00, "tipo": "DESPESA", "categoria": "Lazer"}
]

@app.route('/')
def index():
    # 1. Calcula o Saldo
    saldo = sum(t["valor"] if t["tipo"] == "RECEITA" else -t["valor"] for t in transacoes)
    
    # 2. Calcula onde o dinheiro está indo (Agrupando por Categoria)
    gastos_por_categoria = {}
    for t in transacoes:
        if t["tipo"] == "DESPESA":
            cat = t["categoria"]
            # Se a categoria já existe no dicionário, soma o valor. Se não, cria com o valor atual.
            gastos_por_categoria[cat] = gastos_por_categoria.get(cat, 0) + t["valor"]
            
    return render_template('index.html', transacoes=transacoes, saldo=saldo, resumo=gastos_por_categoria)

@app.route('/adicionar', methods=['POST'])
def adicionar():
    descricao = request.form['descricao']
    valor = float(request.form['valor'])
    tipo = request.form['tipo']
    categoria = request.form['categoria'] # Capturando a nova informação
    
    transacoes.append({
        "descricao": descricao, 
        "valor": valor, 
        "tipo": tipo,
        "categoria": categoria
    })
    
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True)