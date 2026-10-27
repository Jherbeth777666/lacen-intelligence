from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from app import cache
from app.config import Config
from app.db import consultar, consultar_um, engine
from app.seguranca import login_requerido
from etl.anonimizacao import SegredoAusente
from etl.importador import ArquivoIlegivel, ArquivoJaImportado, ColunasAusentes, importar
from etl.lote import importar_lote

bp = Blueprint("importacao", __name__, url_prefix="/importacao")
EXTENSOES = (".csv", ".xlsx", ".xls", ".txt")


@bp.get("/")
@login_requerido("importacao")
def index():
    historico = consultar("""SELECT i.*, u.nome usuario FROM importacoes i LEFT JOIN usuarios u ON u.id=i.usuario_id
                             ORDER BY i.iniciado_em DESC LIMIT 50""")
    return render_template("importacao/index.html", historico=historico)


@bp.post("/")
@login_requerido("importacao")
def enviar():
    arquivos = [f for f in request.files.getlist("arquivo") if f and f.filename]
    fonte = request.form.get("fonte", "AUTO") or "AUTO"
    criar_ubs = request.form.get("criar_ubs") == "1"
    if not arquivos:
        flash("Selecione um arquivo.", "erro")
        return redirect(url_for("importacao.index"))
    ruins = [f.filename for f in arquivos if not f.filename.lower().endswith(EXTENSOES)]
    if ruins:
        flash(f"Formato não suportado: {', '.join(ruins)}. Envie .csv, .xlsx ou .xls.", "erro")
        return redirect(url_for("importacao.index"))
    if fonte not in ("AUTO", "AGENDAMENTOS", "WORKLAB"):
        flash("Selecione o tipo de arquivo.", "erro")
        return redirect(url_for("importacao.index"))
    if fonte == "AUTO" or len(arquivos) > 1:
        return _enviar_lote(arquivos, criar_ubs)
    arq = arquivos[0]
    try:
        rel = importar(engine(), fonte, arq.read(), arq.filename, segredo=Config.PSEUDO_SECRET,
                       usuario_id=session["usuario_id"], criar_ubs=criar_ubs)
    except ArquivoJaImportado as e:
        flash(str(e), "erro")
        return redirect(url_for("importacao.detalhe", iid=e.importacao_id))
    except (ColunasAusentes, ArquivoIlegivel, SegredoAusente) as e:
        flash(str(e), "erro")
        return redirect(url_for("importacao.index"))
    except Exception as e:
        current_app.logger.exception("Falha na importação de %s", arq.filename)
        flash(f"Falha inesperada ao processar o arquivo ({type(e).__name__}). Nada foi gravado. "
              "Se persistir, salve a planilha como .xlsx e tente de novo.", "erro")
        return redirect(url_for("importacao.index"))
    cache.limpar()   # números novos aparecem no painel na hora
    flash(f"Importação concluída: {rel.inseridas} registros novos, {rel.duplicadas} duplicados, "
          f"{rel.rejeitadas} rejeitados.", "ok" if not rel.rejeitadas else "aviso")
    return redirect(url_for("importacao.detalhe", iid=rel.importacao_id))


ROTULO_FONTE = {"AGENDAMENTOS": "Agendamentos", "WORKLAB": "Worklab"}


def _enviar_lote(arquivos, criar_ubs: bool):
    """Vários arquivos (ou tipo 'detectar'): reconhece cada um, junta e importa agenda e Worklab."""
    try:
        lote, resultados = importar_lote(engine(), [(f.filename, f.read()) for f in arquivos],
                                         segredo=Config.PSEUDO_SECRET, usuario_id=session["usuario_id"],
                                         criar_ubs=criar_ubs)
    except SegredoAusente as e:
        flash(str(e), "erro")
        return redirect(url_for("importacao.index"))
    except Exception as e:
        current_app.logger.exception("Falha na importação em lote")
        flash(f"Falha inesperada ao processar os arquivos ({type(e).__name__}). Nada foi gravado.", "erro")
        return redirect(url_for("importacao.index"))
    for a in lote.arquivos:
        if a.erro:
            flash(f"{a.nome}: {a.erro}", "erro")
        elif a.papel == "DESCONHECIDO":
            flash(f"{a.nome}: não reconheci este arquivo (não parece agenda, atendimentos, exames nem unidades) — "
                  "ignorado.", "aviso")
    for p in lote.problemas:
        flash(p, "erro")
    ultimo = None
    for fonte, r in resultados:
        nome = ROTULO_FONTE[fonte]
        if isinstance(r, ArquivoJaImportado):
            flash(f"{nome}: estes arquivos já foram importados (importação #{r.importacao_id}).", "aviso")
        elif isinstance(r, Exception):
            flash(f"{nome}: {r}", "erro")
        else:
            ultimo = r.importacao_id
            flash(f"{nome}: {r.inseridas} registros novos, {r.duplicadas} duplicados, {r.rejeitadas} rejeitados "
                  f"(importação #{r.importacao_id}).", "ok" if not r.rejeitadas else "aviso")
    if not resultados and not lote.problemas:
        flash("Nenhum arquivo de agenda ou do Worklab foi reconhecido.", "erro")
    if lote.identificacao and lote.cruzamento_pct is not None:
        flash(f"Cruzamento agenda × Worklab por {lote.identificacao}: {lote.cruzamento_pct:.0f}% de quem compareceu "
              "foi encontrado no Worklab.", "ok" if lote.cruzamento_pct >= 50 else "aviso")
    cache.limpar()
    return redirect(url_for("importacao.index") if ultimo is None or len(resultados) > 1
                    else url_for("importacao.detalhe", iid=ultimo))


@bp.get("/<int:iid>")
@login_requerido("importacao")
def detalhe(iid):
    imp = consultar_um("SELECT i.*, u.nome usuario FROM importacoes i LEFT JOIN usuarios u ON u.id=i.usuario_id WHERE i.id=:i", i=iid)
    if not imp:
        abort(404)
    resumo = consultar("""SELECT problema, severidade, campo, count(*) qtd FROM importacao_erros
                          WHERE importacao_id=:i GROUP BY 1,2,3 ORDER BY 4 DESC""", i=iid)
    erros = consultar("""SELECT linha, campo, valor, problema, severidade FROM importacao_erros
                         WHERE importacao_id=:i ORDER BY severidade DESC, linha NULLS FIRST LIMIT 300""", i=iid)
    return render_template("importacao/detalhe.html", imp=imp, resumo=resumo, erros=erros)
