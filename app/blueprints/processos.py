"""Gestão de processos: BPMN do processo atual (AS-IS) e futuro (TO-BE) ligados a indicadores reais."""
from flask import Blueprint, render_template

from app.seguranca import login_requerido
from app.services import indicadores as ind
from app.web import contexto_periodo, periodo_da_requisicao

bp = Blueprint("processos", __name__, url_prefix="/processos")


def _mapa_melhorias(per):
    k = ind.kpis(per)
    h = ind.concentracao_horario(per)
    setores = {s["setor"]: s for s in ind.producao_setor(per)}
    pior = min((s for s in setores.values() if s["pct_no_prazo"] is not None),
               key=lambda s: s["pct_no_prazo"], default=None)
    cz = ind.cruzamento_agendamento_producao(per)
    t = ind.triagem_resumo()
    nc = ind.nc_resumo(per)["geral"]
    recoletas = sum(s["recoletas"] for s in setores.values())
    exames = sum(s["exames"] for s in setores.values()) or 1
    return [
        {"etapa": "Agendamento", "tipo": "Ponto de espera",
         "problema": "Todos os pacientes marcados para o mesmo horário",
         "evidencia": f"{h[0]['pct']:.0f}% às {h[0]['horario']}".replace(".", ",") if h else "Sem dados",
         "melhoria": "Agenda escalonada em blocos de 30 min", "automacao": "Regra de distribuição no sistema de agendamento",
         "indicador": "% de agendamentos no horário de pico"},
        {"etapa": "Comparecimento", "tipo": "Desperdício de capacidade",
         "problema": "Vagas ociosas por falta do paciente",
         "evidencia": f"{k['faltas']} faltas ({k['taxa_falta'] or 0:.1f}%)".replace(".", ","),
         "melhoria": "Confirmação ativa 24–48 h antes + overbooking controlado por UBS",
         "automacao": "Lembrete automático (SMS/WhatsApp) a partir da agenda", "indicador": "Taxa de faltas por UBS"},
        {"etapa": "Triagem", "tipo": "Atividade manual",
         "problema": "Conferência manual e fila sem fechamento",
         "evidencia": f"{t['aguardando_mais_24h']} triagem(ns) aguardando > 24 h",
         "melhoria": "Fila com prioridade e prazo visível; fechamento por turno",
         "automacao": "Pré-cadastro a partir do agendamento (sem redigitação)", "indicador": "Tempo até confirmação"},
        {"etapa": "Coleta / transporte", "tipo": "Retrabalho",
         "problema": "Recoleta por amostra inadequada",
         "evidencia": f"{recoletas} recoletas ({100 * recoletas / exames:.1f}% dos exames)".replace(".", ","),
         "melhoria": "Checklist de coleta e controle de temperatura no transporte",
         "automacao": "Registro de NC pré-analítica direto da triagem", "indicador": "Taxa de recoleta por UBS"},
        {"etapa": "Processamento", "tipo": "Gargalo",
         "problema": f"Atraso na liberação{(' — ' + pior['setor']) if pior else ''}",
         "evidencia": (f"{pior['pct_no_prazo']:.0f}% no prazo; TAT médio {pior['tat_medio_h']:.0f} h" if pior else "Sem dados de TAT"),
         "melhoria": "Gestão de estoque de reagentes com ponto de pedido; manutenção preventiva",
         "automacao": "Alerta automático de TAT acima da meta", "indicador": "% de exames liberados no prazo"},
        {"etapa": "Integração de dados", "tipo": "Risco",
         "problema": "Atendimentos que não casam com agendamento",
         "evidencia": f"{cz['pct_atendimentos_sem_agendamento'] or 0:.1f}% sem agendamento".replace(".", ","),
         "melhoria": "Identificador único do paciente entre agenda e Worklab",
         "automacao": "ETL diário com pseudonimização e relatório de inconsistências", "indicador": "% de atendimentos sem agendamento"},
        {"etapa": "Qualidade", "tipo": "Risco",
         "problema": "Não conformidades sem tratamento no prazo",
         "evidencia": f"{nc['vencidas']} vencidas de {nc['abertas']} abertas",
         "melhoria": "Rito semanal de qualidade com 5W2H", "automacao": "Alerta de prazo vencido no painel",
         "indicador": "NCs vencidas / tempo médio de resolução"},
    ]


@bp.get("/")
@login_requerido("processos")
def index():
    per = periodo_da_requisicao()
    return render_template("processos/index.html", melhorias=_mapa_melhorias(per), **contexto_periodo(per))
