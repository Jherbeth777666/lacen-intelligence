"""Cria uma base de DEMONSTRAÇÃO 100% fictícia e a carrega pelo mesmo ETL usado com dados reais.

Os arquivos gerados imitam exports reais (com sujeira proposital: UBS escrita de
formas diferentes, datas inválidas, duplicidades, siglas desconhecidas) para que
a demonstração mostre o tratamento de dados funcionando.

Padrões embutidos (para o Analista IA ter o que descobrir):
  • UBS Vila Esperança: faltas sobem mês a mês (≈20% → ≈40%)
  • UBS Jardim Primavera: absenteísmo estruturalmente alto (≈45%)
  • Segundas e sextas com mais faltas
  • ~92% dos agendamentos marcados às 07:00 (fila na abertura)
  • Hormônios: tempo de liberação piora em ago/set (falta de reagente) + NCs coerentes
  • UBS Santa Luzia: mais recoletas (problema de transporte/hemólise)

Uso:  python -m scripts.demo                 (banco local: recria tudo e carrega)
      python -m scripts.demo --sem-recriar   (Supabase/banco compartilhado: só cria o que falta
                                              e carrega se ainda não houver dados)
"""
from __future__ import annotations

import pathlib
import random
import subprocess
import sys
from datetime import date, datetime, timedelta

import bcrypt
import pandas as pd
from sqlalchemy import create_engine, text

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from app.config import Config  # noqa: E402
from app.db import criar_engine  # noqa: E402
from etl.anonimizacao import chave_paciente, pseudo_id  # noqa: E402
from etl.importador import importar  # noqa: E402

rnd = random.Random(42)
SAIDA = RAIZ / "data" / "demo"
INICIO, FIM = date(2026, 4, 1), date(2026, 9, 30)
SENHA_DEMO = "Lacen@2026"

UBS = [  # nome, peso de volume, taxa de falta base
    ("UBS VILA ESPERANÇA", 1.3, None), ("UBS JARDIM PRIMAVERA", 1.1, 0.45), ("UBS SANTA LUZIA", 1.4, 0.19),
    ("UBS BAIRRO NOVO", 1.6, 0.17), ("UBS CIDADE ALTA", 1.2, 0.22), ("UBS PARQUE DOS IPÊS", 0.9, 0.18),
    ("UBS SÃO RAIMUNDO", 1.5, 0.24), ("UBS RECANTO VERDE", 0.8, 0.16), ("UBS MORADA DO SOL", 1.0, 0.21),
    ("UBS BOA VISTA", 0.7, 0.26), ("UBS NOVA AURORA", 1.1, 0.20), ("UBS LAGOA AZUL", 0.6, 0.23),
    ("UBS PLANALTO", 0.9, 0.18), ("UBS VILA NOVA", 1.0, 0.25), ("CENTRO DE ESPECIALIDADES CENTRAL", 0.8, 0.15),
    ("CTA MUNICIPAL", 0.3, 0.20), ("ESPAÇO DO IDOSO", 0.4, 0.17), ("UNIDADE PRISIONAL REGIONAL", 0.4, 0.12),
]
FALTA_VILA_ESPERANCA = {4: 0.19, 5: 0.20, 6: 0.22, 7: 0.26, 8: 0.32, 9: 0.40}
AJUSTE_DIA = {0: 0.06, 1: 0.0, 2: -0.02, 3: 0.0, 4: 0.04}

# Frequência relativa observada por sigla (agregado anônimo do export real) — define o mix de exames
FREQ_EXAMES = {'HEMO': 1098, 'GLI': 1038, 'LIPI': 930, 'CRE': 895, 'URE': 817, 'TGO': 719, 'TGP': 713, 'ACUR': 390,
               'HB1AC': 379, 'FERRO': 272, 'TSH': 213, 'VDRL': 204, 'T4LA': 187, 'FERRI': 180, 'PCRDL': 169,
               'HIVTR': 133, 'NA': 131, 'HBSAG': 116, 'B12': 112, 'ABO': 104, '25DVI': 99, 'HCV': 92, 'BILI': 88,
               'VHS': 70, 'TOXI': 62, 'PSA': 59, 'PSALI': 58, 'AMILA': 56, 'FAL': 42, 'FSH': 38, 'ALBU': 34,
               'CA': 32, 'PROT': 31, 'LH': 31, 'BHCG': 29, 'LIPAS': 29, 'GAMGT': 26, 'ESTRA': 23, 'ASLO': 21,
               'T4T': 20, 'T3': 11, 'TESTT': 8, 'MG': 5, 'CPK': 5, 'FOLI': 5}
TAT_SETOR = {"Hematologia": (5, 20), "Bioquímica": (7, 26), "Hormônios": (26, 66), "Imunologia/Sorologia": (36, 90)}

NOMES = ("ANA MARIA JOSÉ JOÃO FRANCISCA ANTÔNIO FRANCISCO LUCAS RAIMUNDA PEDRO JULIANA CARLOS ALINE "
         "MARCOS LUANA PAULO BRUNA RAFAEL TATIANA JORGE CLÁUDIA EDUARDO SÔNIA FELIPE PATRÍCIA DANIEL "
         "LETÍCIA GABRIEL VITÓRIA MATEUS RITA SAMUEL CARLA IGOR DÉBORA RENATO ELISA TIAGO NÁDIA").split()
SOBRENOMES = ("SILVA SOUSA SANTOS OLIVEIRA PEREIRA LIMA COSTA CARVALHO RIBEIRO ALMEIDA ARAÚJO NASCIMENTO "
              "MENDES BARBOSA ROCHA FERREIRA CASTRO MORAES CAMPOS TEIXEIRA MOURA CARDOSO FREITAS PINTO").split()


def pessoa():
    nome = f"{rnd.choice(NOMES)} {rnd.choice(SOBRENOMES)} {rnd.choice(SOBRENOMES)}"
    nasc = date(1940, 1, 1) + timedelta(days=rnd.randint(0, 365 * 80))
    cns = str(rnd.choice([7, 8, 9])) + "".join(str(rnd.randint(0, 9)) for _ in range(14))
    return {"nome": nome, "nasc": nasc, "cns": cns}


def gerar_cpf():
    n = [rnd.randint(0, 9) for _ in range(9)]
    for k in (10, 11):
        d = sum(v * (k - i) for i, v in enumerate(n)) * 10 % 11 % 10
        n.append(d)
    return "".join(map(str, n))


def variante_ubs(nome: str) -> str:
    r = rnd.random()
    if r < 0.05:
        return nome.title()
    if r < 0.08:
        return nome.replace("UBS ", "U.B.S. ")
    if r < 0.10:
        return nome.replace("Ê", "E").replace("Ç", "C").replace("Ã", "A").lower()
    if r < 0.12:
        return "  " + nome + " "
    return nome


def dias_uteis(ini, fim):
    d = ini
    while d <= fim:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


# ----------------------------------------------------------------------------
def gerar_arquivos(hoje: date):
    SAIDA.mkdir(parents=True, exist_ok=True)
    populacao = [pessoa() for _ in range(9000)]
    pesos_ubs = [u[1] for u in UBS]
    ag_linhas, wl_linhas = [], []
    seq_atend = 3_100_000
    siglas, pesos_ex = zip(*FREQ_EXAMES.items())

    def novo_atendimento(dia, p, ubs_nome):
        nonlocal seq_atend
        seq_atend += rnd.randint(1, 4)
        n = max(1, min(14, int(rnd.gauss(7.5, 2.8))))
        escolhidos = set()
        while len(escolhidos) < n:
            escolhidos.add(rnd.choices(siglas, pesos_ex)[0])
        for s in escolhidos:
            wl_linhas.append({"Atendimento": seq_atend, "Data Atendimento": dia.strftime("%d/%m/%Y"),
                              "Nome Paciente": p["nome"], "Data Nascimento": p["nasc"].strftime("%d/%m/%Y"),
                              "Unidade": ubs_nome if (ubs_nome and rnd.random() < 0.6) else "",
                              "Convênio": "SUS" if rnd.random() < 0.97 else rnd.choice(["PARTICULAR", "CONVÊNIO MUNICIPAL"]),
                              "Sigla Exame": s, "_ubs_real": ubs_nome, "_dia": dia})

    for dia in dias_uteis(INICIO, max(hoje, FIM) + timedelta(days=14)):
        futuro = dia > hoje
        if FIM < dia <= hoje:
            continue  # a base histórica fecha em 30/09; depois de hoje só agenda futura
        volume = int(rnd.gauss(82, 12) * (0.85 if dia.weekday() == 4 else 1))
        for _ in range(volume):
            ui = rnd.choices(range(len(UBS)), pesos_ubs)[0]
            nome_ubs, _, falta_base = UBS[ui]
            p_falta = (FALTA_VILA_ESPERANCA.get(dia.month, 0.4) if falta_base is None else falta_base) \
                + AJUSTE_DIA[dia.weekday()]
            p = rnd.choice(populacao)
            if futuro:
                status = "Agendado"
            else:
                r = rnd.random()
                status = "Pendente" if r < 0.005 else ("Faltou" if r < 0.005 + p_falta else "Compareceu")
            hora = rnd.choices(["07:00", "07:30", "08:00"], [92, 5, 3])[0]
            ag_linhas.append({"Data": dia.strftime("%d/%m/%Y"), "Horário": hora, "Unidade": variante_ubs(nome_ubs),
                              "Paciente": p["nome"], "Data Nascimento": p["nasc"].strftime("%d/%m/%Y"),
                              "CNS": p["cns"], "Tipo": "Urgência" if rnd.random() < 0.01 else "Exame de Rotina",
                              "Status": status,
                              "Observações": rnd.choice(["", "", "", "sem comorbidade", "paciente em jejum"]),
                              "_mes": dia.strftime("%Y-%m")})
            if status == "Compareceu" and rnd.random() < 0.93:
                novo_atendimento(dia, p, nome_ubs)
        if not futuro and dia <= FIM:
            for _ in range(int(volume * 0.12)):  # demanda espontânea (sem agendamento)
                novo_atendimento(dia, rnd.choice(populacao), None)

    # Resultado/liberação de cada exame
    exames_db = pd.read_sql("SELECT sigla, setor, valor_ref FROM analitico.dim_exame",
                            criar_engine(Config.DATABASE_URL)).set_index("sigla")
    corte = datetime.combine(FIM, datetime.max.time())
    for l in wl_linhas:
        setor = exames_db.loc[l["Sigla Exame"], "setor"]
        lo, hi = TAT_SETOR.get(setor, (24, 72))
        if setor == "Hormônios" and l["_dia"].month in (8, 9):
            lo, hi = lo + 30, hi + 60  # gargalo: falta de reagente
        coleta = datetime.combine(l["_dia"], datetime.min.time()) + timedelta(hours=7)
        lib = coleta + timedelta(hours=rnd.uniform(lo, hi))
        l["Valor"] = f"{exames_db.loc[l['Sigla Exame'], 'valor_ref']:.2f}".replace(".", ",")
        p_recoleta = 0.05 if l["_ubs_real"] == "UBS SANTA LUZIA" else 0.01
        r = rnd.random()
        if r < p_recoleta:
            l["Situação"], l["Data Liberação"] = "Recoleta", ""
        elif r < p_recoleta + 0.005:
            l["Situação"], l["Data Liberação"] = "Cancelado", ""
        elif lib > corte:
            l["Situação"], l["Data Liberação"] = "Em análise", ""
        else:
            l["Situação"], l["Data Liberação"] = "Liberado", lib.strftime("%d/%m/%Y %H:%M")

    ag = pd.DataFrame(ag_linhas)
    wl = pd.DataFrame(wl_linhas)
    wl["_mes"] = wl["_dia"].map(lambda d: d.strftime("%Y-%m"))

    # ---- sujeira proposital ----
    sujos = ag.sample(frac=0.003, random_state=1).index
    ag.loc[sujos, "Data"] = "31/06/2026"
    ag.loc[ag.sample(frac=0.002, random_state=2).index, "Unidade"] = "UBS SEM CADASTRO"
    ag.loc[ag.sample(frac=0.002, random_state=3).index, ["Paciente", "CNS", "Data Nascimento"]] = ""
    ag.loc[ag.sample(frac=0.002, random_state=4).index, "Status"] = ""
    ag = pd.concat([ag, ag.sample(frac=0.005, random_state=5)]).sort_index()
    wl.loc[wl.sample(frac=0.002, random_state=6).index, "Sigla Exame"] = rnd.choice(["CONFERIDO", "XPTO", "HEMOGR"])
    wl = pd.concat([wl, wl.sample(frac=0.002, random_state=7)]).sort_index()

    arquivos = []
    for mes, g in ag.groupby("_mes"):
        caminho = SAIDA / f"agendamentos_{mes}.xlsx"
        g.drop(columns=["_mes"]).to_excel(caminho, index=False)
        arquivos.append(("AGENDAMENTOS", caminho))
    for mes, g in wl.groupby("_mes"):
        caminho = SAIDA / f"worklab_{mes}.csv"
        g.drop(columns=["_mes", "_ubs_real", "_dia"]).to_csv(caminho, sep=";", index=False, encoding="utf-8-sig")
        arquivos.append(("WORKLAB", caminho))
    return arquivos


# ----------------------------------------------------------------------------
def popular_cadastros(eng, hoje: date):
    with eng.begin() as c:
        for nome, _, _ in UBS:
            tipo = ("UBS" if nome.startswith("UBS") else "CENTRO_ESPECIALIDADES" if "ESPECIALIDADES" in nome
                    else "CTA" if nome.startswith("CTA") else "ESPACO" if nome.startswith("ESPA")
                    else "PRISIONAL")
            c.execute(text("INSERT INTO ubs (nome, tipo) VALUES (:n,:t) ON CONFLICT DO NOTHING"), {"n": nome, "t": tipo})
        h = bcrypt.hashpw(SENHA_DEMO.encode(), bcrypt.gensalt()).decode()
        usuarios = [("Administrador Demo", "admin@lacen.demo", "admin"), ("Gestora Demo", "gestor@lacen.demo", "gestor"),
                    ("Triagem Demo", "triagem@lacen.demo", "triagem"), ("Qualidade Demo", "qualidade@lacen.demo", "qualidade")]
        for n, e, p in usuarios:
            c.execute(text("INSERT INTO usuarios (nome,email,senha_hash,perfil) VALUES (:n,:e,:h,:p) ON CONFLICT DO NOTHING"),
                      {"n": n, "e": e, "h": h, "p": p})
        medicos = ["DRA. HELENA DUARTE", "DR. MARCOS VIEIRA", "DRA. CAMILA PRADO", "DR. ROBERTO NUNES",
                   "DRA. LÍVIA MONTEIRO", "DR. ANDRÉ FONTES", "DRA. BEATRIZ SALES", "DR. CAIO RANGEL"]
        for i, m in enumerate(medicos):
            c.execute(text("INSERT INTO profissionais (nome, registro) VALUES (:n,:r) ON CONFLICT DO NOTHING"),
                      {"n": m, "r": str(90100 + i * 37)})

        ubs_ids = [r[0] for r in c.execute(text("SELECT id FROM ubs ORDER BY id"))]
        prof_ids = [r[0] for r in c.execute(text("SELECT id FROM profissionais"))]
        uid = {r.perfil: r.id for r in c.execute(text("SELECT id, perfil FROM usuarios"))}
        exame_ids = [r[0] for r in c.execute(text("SELECT id FROM exames WHERE sigla = ANY(:s)"),
                                              {"s": list(FREQ_EXAMES)[:25]})]
        c.execute(text("SELECT set_config('app.usuario_id', :u, true)"), {"u": str(uid["triagem"])})

        # Pacientes e triagens dos últimos dias (fila operacional)
        for i in range(36):
            p = pessoa()
            cpf = gerar_cpf()
            ident, _ = chave_paciente(cpf=cpf)
            pid = c.execute(text("""INSERT INTO pacientes (pseudo_id, nome, cpf, cns, data_nascimento, sexo, endereco, ubs_id, criado_por)
                                    VALUES (:ps,:n,:cpf,:cns,:dn,:sx,:end,:u,:cp) RETURNING id"""),
                            {"ps": pseudo_id(ident, Config.PSEUDO_SECRET), "n": p["nome"], "cpf": cpf, "cns": p["cns"],
                             "dn": p["nasc"], "sx": rnd.choice("MF"), "end": f"RUA FICTÍCIA {rnd.randint(1, 300)}, Nº {rnd.randint(1, 999)}",
                             "u": rnd.choice(ubs_ids), "cp": uid["triagem"]}).scalar()
            dias_atras = i // 8
            quando = datetime.combine(hoje - timedelta(days=dias_atras), datetime.min.time()) + timedelta(
                hours=7, minutes=rnd.randint(0, 240))
            if dias_atras == 0:
                situacao = "AGUARDANDO"
            elif dias_atras >= 2 and i % 9 == 0:
                situacao = "AGUARDANDO"  # esquecidas na fila → alerta
            else:
                situacao = rnd.choice(["CONFIRMADA", "COLETADA", "COLETADA"])
            conf = quando + timedelta(minutes=rnd.randint(5, 90)) if situacao != "AGUARDANDO" else None
            tid = c.execute(text("""INSERT INTO triagens (paciente_id, ubs_id, profissional_id, data_triagem, prioridade,
                                     situacao, responsavel_id, confirmado_em, confirmado_por)
                                     VALUES (:p,:u,:pr,:q,:pri,:s,:r,:ce,:cp) RETURNING id"""),
                            {"p": pid, "u": rnd.choice(ubs_ids), "pr": rnd.choice(prof_ids), "q": quando,
                             "pri": rnd.choices(["ROTINA", "PRIORITARIA", "URGENTE"], [85, 12, 3])[0],
                             "s": situacao, "r": uid["triagem"], "ce": conf,
                             "cp": uid["triagem"] if conf else None}).scalar()
            for e in rnd.sample(exame_ids, rnd.randint(2, 7)):
                c.execute(text("INSERT INTO triagem_exames VALUES (:t,:e)"), {"t": tid, "e": e})

        # Não conformidades + 5W2H
        setor = {r.nome: r.id for r in c.execute(text("SELECT id, nome FROM setores"))}
        ubs_nome = {r.nome: r.id for r in c.execute(text("SELECT id, nome FROM ubs"))}
        modelos = [
            ("PRE_ANALITICA", "Amostra hemolisada", "Hematologia", "UBS SANTA LUZIA", "Transporte sem refrigeração adequada"),
            ("PRE_ANALITICA", "Transporte fora da temperatura", None, "UBS SANTA LUZIA", "Caixa térmica sem controle de temperatura"),
            ("PRE_ANALITICA", "Amostra insuficiente", "Bioquímica", None, "Coleta com tubo de volume incorreto"),
            ("PRE_ANALITICA", "Identificação divergente", None, None, "Etiqueta preenchida à mão"),
            ("PRE_ANALITICA", "Requisição incompleta", None, "UBS JARDIM PRIMAVERA", "Formulário sem campo obrigatório"),
            ("ANALITICA", "Falta de reagente", "Hormônios", None, "Atraso na entrega do fornecedor"),
            ("ANALITICA", "Equipamento parado", "Hormônios", None, "Manutenção preventiva não realizada"),
            ("ANALITICA", "Controle de qualidade fora do limite", "Bioquímica", None, "Calibração vencida"),
            ("POS_ANALITICA", "Atraso na liberação de laudo", "Hormônios", None, "Fila acumulada após falta de reagente"),
            ("POS_ANALITICA", "Laudo com erro de digitação", None, None, "Transcrição manual"),
            ("ADMINISTRATIVA", "Cadastro incorreto no agendamento", None, "UBS VILA ESPERANÇA", "Sem conferência de telefone/endereço"),
            ("ADMINISTRATIVA", "Paciente atendido sem agendamento", None, None, "Demanda espontânea sem registro prévio"),
        ]
        d = INICIO
        while d <= hoje:
            peso_mes = 1.0 + (0.8 if d.month in (8, 9) else 0)
            if rnd.random() < 0.28 * peso_mes:
                etapa, cat, st, ub, causa = rnd.choice(modelos)
                if d.month in (8, 9) and rnd.random() < 0.4:
                    etapa, cat, st, ub, causa = rnd.choice(modelos[5:9])
                idade = (hoje - d).days
                status = ("CONCLUIDA" if idade > 45 and rnd.random() < 0.85 else
                          rnd.choice(["ABERTA", "EM_TRATAMENTO", "CONCLUIDA"]) if idade > 10 else "ABERTA")
                prazo = d + timedelta(days=rnd.choice([7, 15, 30]))
                concl = min(d + timedelta(days=rnd.randint(2, 40)), hoje) if status == "CONCLUIDA" else None
                nc = c.execute(text("""INSERT INTO nao_conformidades (data, setor_id, ubs_id, etapa, categoria, problema, causa,
                                       responsavel_id, prioridade, prazo, status, acao_corretiva, concluida_em, criado_por)
                                       VALUES (:d,:s,:u,:e,:c,:p,:ca,:r,:pr,:pz,:st,:ac,:co,:cp) RETURNING id"""),
                               {"d": d, "s": setor.get(st), "u": ubs_nome.get(ub), "e": etapa, "c": cat,
                                "p": f"{cat} registrada{' na ' + ub.title() if ub else ''}.", "ca": causa,
                                "r": uid["qualidade"], "pr": rnd.choices(["BAIXA", "MEDIA", "ALTA", "CRITICA"], [2, 5, 3, 1])[0],
                                "pz": prazo, "st": status,
                                "ac": "Ação corretiva aplicada e verificada." if status == "CONCLUIDA" else None,
                                "co": concl, "cp": uid["qualidade"]}).scalar()
                if rnd.random() < 0.6:
                    c.execute(text("""INSERT INTO planos_acao (nc_id, o_que, por_que, onde, quando, quem, como, quanto, status)
                                      VALUES (:n,:o,:pq,:on,:q,:qm,:co,:qt,:st)"""),
                              {"n": nc, "o": f"Eliminar causa: {causa.lower()}", "pq": f"Evitar reincidência de '{cat.lower()}'",
                               "on": ub.title() if ub else (st or "Laboratório"), "q": prazo,
                               "qm": "Coordenação da qualidade", "co": "Revisar procedimento, treinar equipe e monitorar por 30 dias",
                               "qt": rnd.choice(["Sem custo", "R$ 350,00", "R$ 1.200,00", "A orçar"]),
                               "st": "CONCLUIDA" if status == "CONCLUIDA" else rnd.choice(["PLANEJADA", "EM_ANDAMENTO"])})
            d += timedelta(days=1)


def main():
    hoje = date.today()
    sem_recriar = "--sem-recriar" in sys.argv or "supabase" in Config.DATABASE_URL
    if sem_recriar:
        print("1/4 Atualizando estrutura do banco (sem apagar nada)…")
        subprocess.run([sys.executable, "-m", "scripts.migrar"], cwd=RAIZ, check=True)
    else:
        print("1/4 Recriando banco…")
        subprocess.run([sys.executable, "-m", "scripts.migrar", "--recriar"], cwd=RAIZ, check=True)
    eng = criar_engine(Config.DATABASE_URL)
    if sem_recriar:
        with eng.connect() as c:
            if c.execute(text("SELECT (SELECT count(*) FROM agendamentos) + (SELECT count(*) FROM usuarios)")).scalar():
                sys.exit("✗ O banco já tem dados — a base de demonstração não foi carregada para não misturar.")
    print("2/4 Cadastros, triagens e não conformidades fictícios…")
    popular_cadastros(eng, hoje)
    print("3/4 Gerando arquivos brutos em data/demo/…")
    arquivos = gerar_arquivos(hoje)
    print("4/4 Importando pelo ETL…")
    for fonte, caminho in arquivos:
        rel = importar(eng, fonte, caminho.read_bytes(), caminho.name, segredo=Config.PSEUDO_SECRET, hoje=hoje)
        r = rel.resumo()
        print(f"   {caminho.name:<28} lidas={r['linhas_lidas']:>6} inseridas={r['inseridas']:>6} "
              f"dup={r['duplicadas']:>4} rejeitadas={r['rejeitadas']:>3} avisos={r['avisos']:>3}")
    print(f"\n✓ Base de demonstração pronta. Login: admin@lacen.demo / {SENHA_DEMO} "
          "(também gestor@, triagem@, qualidade@)")


if __name__ == "__main__":
    main()
