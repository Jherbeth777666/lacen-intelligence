"""Módulo de Triagem — evolução do site original (cadastro → fila → confirmação).

Mudanças em relação ao projeto anterior:
  • um registro de triagem com N exames (tabela triagem_exames), em vez de uma linha por exame;
  • médicos, UBS e exames vêm do banco (não ficam fixos no HTML);
  • quem registrou/confirmou e quando ficam gravados (e auditados por trigger);
  • CPF validado e exibido sempre mascarado; paciente recebe pseudo_id para a camada analítica.
"""
from datetime import date

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import text

from app.config import Config
from app.db import consultar, consultar_um, transacao
from app.seguranca import login_requerido, mascarar_cns, mascarar_cpf, pode
from app.services import indicadores as ind
from app.web import dados_json
from etl.anonimizacao import chave_paciente, pseudo_id
from etl.normalizacao import cpf_valido, so_digitos

bp = Blueprint("triagem", __name__, url_prefix="/triagem")

TRANSICOES = {"confirmar": ({"AGUARDANDO"}, "CONFIRMADA"),
              "coletar": ({"CONFIRMADA", "AGUARDANDO"}, "COLETADA"),
              "cancelar": ({"AGUARDANDO", "CONFIRMADA"}, "CANCELADA")}


def _idade(nasc: date | None) -> int | None:
    if not nasc:
        return None
    h = date.today()
    return h.year - nasc.year - ((h.month, h.day) < (nasc.month, nasc.day))


@bp.get("/")
@login_requerido("triagem")
def fila():
    situacao = request.args.get("situacao", "ABERTAS")
    busca = (request.args.get("busca") or "").strip()
    dias = request.args.get("dias", "7")
    dias = int(dias) if dias.isdigit() and int(dias) <= 365 else 7
    filtro_sit = {"ABERTAS": "t.situacao IN ('AGUARDANDO','CONFIRMADA')", "TODAS": "TRUE"}.get(
        situacao, "t.situacao = :sit")
    linhas = consultar(f"""
        SELECT t.id, t.data_triagem, t.prioridade, t.situacao, t.confirmado_em,
               p.nome, p.data_nascimento, u.nome AS ubs, pr.nome AS profissional,
               round((EXTRACT(EPOCH FROM (now() - t.data_triagem))/3600)::numeric,1) AS horas,
               (SELECT string_agg(e.sigla, ' ' ORDER BY e.sigla) FROM triagem_exames te JOIN exames e ON e.id=te.exame_id
                WHERE te.triagem_id=t.id) AS exames
        FROM triagens t JOIN pacientes p ON p.id=t.paciente_id JOIN ubs u ON u.id=t.ubs_id
        LEFT JOIN profissionais pr ON pr.id=t.profissional_id
        WHERE {filtro_sit} AND t.data_triagem >= CURRENT_DATE - :dias
          AND (:busca = '' OR p.nome ILIKE '%' || :busca || '%')
        ORDER BY CASE t.prioridade WHEN 'URGENTE' THEN 0 WHEN 'PRIORITARIA' THEN 1 ELSE 2 END,
                 CASE t.situacao WHEN 'AGUARDANDO' THEN 0 WHEN 'CONFIRMADA' THEN 1 ELSE 2 END, t.data_triagem
        LIMIT 300""", sit=situacao, dias=dias, busca=busca)
    for l in linhas:
        l["idade"] = _idade(l.pop("data_nascimento"))
    historico = ind.triagem_historico(30)
    return render_template("triagem/fila.html", linhas=linhas, resumo=ind.triagem_resumo(),
                           situacao=situacao, busca=busca, dias=dias, historico=historico,
                           graficos=dados_json({"diario": historico["diario"]}))


def _opcoes():
    return {"ubs": consultar("SELECT id, nome FROM ubs WHERE ativo ORDER BY nome"),
            "profissionais": consultar("SELECT id, nome, conselho, registro, uf FROM profissionais WHERE ativo ORDER BY nome"),
            "exames": consultar("""SELECT e.id, e.sigla, e.nome, COALESCE(s.nome,'Não classificado') setor
                                   FROM exames e LEFT JOIN setores s ON s.id=e.setor_id
                                   WHERE e.ativo AND s.nome <> 'Não classificado' ORDER BY s.nome, e.nome""")}


def _validar(f) -> tuple[dict, list[str]]:
    erros = []
    d = {k: (f.get(k) or "").strip() for k in ("paciente_id", "nome", "cpf", "cns", "data_nascimento", "sexo",
                                                "endereco", "ubs_id", "profissional_id", "prioridade", "observacoes")}
    d["exames"] = [int(x) for x in f.getlist("exames") if x.isdigit()]
    d["cpf"], d["cns"] = so_digitos(d["cpf"]), so_digitos(d["cns"])
    if not d["paciente_id"]:
        if len(d["nome"].split()) < 2:
            erros.append("Informe o nome completo do paciente.")
        if d["cpf"] and not cpf_valido(d["cpf"]):
            erros.append("CPF inválido.")
        if d["cns"] and len(d["cns"]) != 15:
            erros.append("CNS deve ter 15 dígitos.")
        if not d["cpf"] and not d["cns"]:
            erros.append("Informe CPF ou Cartão SUS.")
        try:
            nasc = date.fromisoformat(d["data_nascimento"])
            if not date(1900, 1, 1) < nasc <= date.today():
                erros.append("Data de nascimento fora do intervalo válido.")
        except ValueError:
            erros.append("Data de nascimento inválida.")
        if d["sexo"] not in ("M", "F"):
            erros.append("Selecione o sexo.")
    if not d["ubs_id"].isdigit():
        erros.append("Selecione a UBS de origem.")
    if d["prioridade"] not in ("ROTINA", "PRIORITARIA", "URGENTE"):
        erros.append("Selecione a prioridade.")
    if not d["exames"]:
        erros.append("Selecione pelo menos um exame.")
    return d, erros


@bp.route("/nova", methods=["GET", "POST"])
@login_requerido("triagem_editar")
def nova():
    if request.method == "POST":
        d, erros = _validar(request.form)
        if erros:
            for e in erros:
                flash(e, "erro")
            return render_template("triagem/nova.html", f=d, **_opcoes()), 400
        with transacao(limpa_cache=False) as c:
            pid = int(d["paciente_id"]) if d["paciente_id"].isdigit() else None
            if pid is None and d["cpf"]:
                pid = c.execute(text("SELECT id FROM pacientes WHERE cpf=:c"), {"c": d["cpf"]}).scalar()
            if pid is None and d["cns"]:
                pid = c.execute(text("SELECT id FROM pacientes WHERE cns=:c"), {"c": d["cns"]}).scalar()
            if pid is None:
                nasc = date.fromisoformat(d["data_nascimento"])
                ident, _ = chave_paciente(cpf=d["cpf"], nome=d["nome"], nascimento=nasc, cns=d["cns"])
                pid = c.execute(text("""
                    INSERT INTO pacientes (pseudo_id, nome, cpf, cns, data_nascimento, sexo, endereco, ubs_id, criado_por)
                    VALUES (:ps, :n, NULLIF(:cpf,''), NULLIF(:cns,''), :dn, :sx, NULLIF(:end,''), :u, :cp) RETURNING id"""),
                    {"ps": pseudo_id(ident, Config.PSEUDO_SECRET), "n": " ".join(d["nome"].upper().split()),
                     "cpf": d["cpf"], "cns": d["cns"], "dn": nasc, "sx": d["sexo"],
                     "end": d["endereco"].upper(), "u": int(d["ubs_id"]), "cp": session["usuario_id"]}).scalar()
            tid = c.execute(text("""
                INSERT INTO triagens (paciente_id, ubs_id, profissional_id, prioridade, observacoes, responsavel_id)
                VALUES (:p, :u, :pr, :pri, NULLIF(:obs,''), :r) RETURNING id"""),
                {"p": pid, "u": int(d["ubs_id"]), "pr": int(d["profissional_id"]) if d["profissional_id"].isdigit() else None,
                 "pri": d["prioridade"], "obs": d["observacoes"][:500], "r": session["usuario_id"]}).scalar()
            c.execute(text("INSERT INTO triagem_exames (triagem_id, exame_id) VALUES (:t, :e)"),
                      [{"t": tid, "e": e} for e in sorted(set(d["exames"]))])
        flash(f"Triagem #{tid} registrada com {len(set(d['exames']))} exame(s).", "ok")
        return redirect(url_for("triagem.fila"))
    return render_template("triagem/nova.html", f={"prioridade": "ROTINA"}, **_opcoes())


@bp.post("/api/paciente")
@login_requerido("triagem_editar")
def buscar_paciente():
    """Busca por CPF/CNS via POST (o documento não aparece em URL nem em log de acesso)."""
    doc = so_digitos((request.get_json(silent=True) or {}).get("documento"))
    if len(doc) not in (11, 15):
        return jsonify({"encontrado": False})
    p = consultar_um("""SELECT id, nome, data_nascimento, sexo, ubs_id FROM pacientes
                        WHERE (length(:d)=11 AND cpf=:d) OR (length(:d)=15 AND cns=:d)""", d=doc)
    if not p:
        return jsonify({"encontrado": False})
    return jsonify({"encontrado": True, "id": p["id"], "nome": p["nome"], "idade": _idade(p["data_nascimento"]),
                    "sexo": p["sexo"], "ubs_id": p["ubs_id"]})


@bp.get("/<int:tid>")
@login_requerido("triagem")
def detalhe(tid):
    t = consultar_um("""
        SELECT t.*, p.nome, p.cpf, p.cns, p.data_nascimento, p.sexo, p.endereco, p.pseudo_id,
               u.nome ubs, pr.nome profissional, pr.conselho, pr.registro, pr.uf,
               ur.nome responsavel, uc.nome confirmador
        FROM triagens t JOIN pacientes p ON p.id=t.paciente_id JOIN ubs u ON u.id=t.ubs_id
        LEFT JOIN profissionais pr ON pr.id=t.profissional_id
        LEFT JOIN usuarios ur ON ur.id=t.responsavel_id LEFT JOIN usuarios uc ON uc.id=t.confirmado_por
        WHERE t.id=:i""", i=tid)
    if not t:
        abort(404)
    exames = consultar("""SELECT e.sigla, e.nome, COALESCE(s.nome,'—') setor FROM triagem_exames te
                          JOIN exames e ON e.id=te.exame_id LEFT JOIN setores s ON s.id=e.setor_id
                          WHERE te.triagem_id=:i ORDER BY s.nome, e.nome""", i=tid)
    historico = consultar("""SELECT a.quando, a.acao, us.nome usuario, a.depois->>'situacao' situacao
                             FROM auditoria a LEFT JOIN usuarios us ON us.id=a.usuario_id
                             WHERE a.tabela='triagens' AND a.registro_id=:i ORDER BY a.quando""", i=str(tid))
    t["cpf_mascarado"], t["cns_mascarado"] = mascarar_cpf(t.pop("cpf")), mascarar_cns(t.pop("cns"))
    t["idade"] = _idade(t["data_nascimento"])
    if not pode("triagem_editar"):
        t["endereco"] = None  # gestor vê a fila, mas não o endereço do paciente
    return render_template("triagem/detalhe.html", t=t, exames=exames, historico=historico)


@bp.post("/<int:tid>/<acao>")
@login_requerido("triagem_editar")
def mudar_situacao(tid, acao):
    if acao not in TRANSICOES:
        abort(404)
    origem, destino = TRANSICOES[acao]
    with transacao(limpa_cache=False) as c:
        atual = c.execute(text("SELECT situacao FROM triagens WHERE id=:i FOR UPDATE"), {"i": tid}).scalar()
        if atual is None:
            abort(404)
        if atual not in origem:
            flash(f"Triagem #{tid} está '{atual.lower()}' e não pode ser alterada para '{destino.lower()}'.", "erro")
        else:
            c.execute(text(f"""UPDATE triagens SET situacao=:s
                               {", confirmado_em=now(), confirmado_por=:u" if destino == "CONFIRMADA" else ""}
                               WHERE id=:i"""), {"s": destino, "u": session["usuario_id"], "i": tid})
            flash(f"Triagem #{tid}: {destino.lower()}.", "ok")
    voltar = request.form.get("voltar") or ""
    return redirect(voltar if voltar.startswith("/triagem") else url_for("triagem.fila"))
