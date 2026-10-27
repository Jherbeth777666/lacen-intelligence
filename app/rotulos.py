"""Rótulos legíveis para os domínios do banco (usados nas telas e nas respostas da IA)."""

ETAPA = {"PRE_ANALITICA": "Pré-analítica", "ANALITICA": "Analítica",
         "POS_ANALITICA": "Pós-analítica", "ADMINISTRATIVA": "Administrativa"}
SITUACAO_TRIAGEM = {"AGUARDANDO": "Aguardando", "CONFIRMADA": "Confirmada", "COLETADA": "Coletada",
                    "CANCELADA": "Cancelada"}
PRIORIDADE_TRIAGEM = {"ROTINA": "Rotina", "PRIORITARIA": "Prioritária", "URGENTE": "Urgente"}
STATUS_NC = {"ABERTA": "Aberta", "EM_TRATAMENTO": "Em tratamento", "CONCLUIDA": "Concluída", "CANCELADA": "Cancelada"}
PRIORIDADE_NC = {"BAIXA": "Baixa", "MEDIA": "Média", "ALTA": "Alta", "CRITICA": "Crítica"}
STATUS_PLANO = {"PLANEJADA": "Planejada", "EM_ANDAMENTO": "Em andamento", "CONCLUIDA": "Concluída",
                "ATRASADA": "Atrasada"}
PERFIL = {"admin": "Administrador", "gestor": "Gestor", "triagem": "Triagem", "qualidade": "Qualidade"}
STATUS_IMPORTACAO = {"PROCESSANDO": "Processando", "CONCLUIDA": "Concluída",
                     "CONCLUIDA_COM_ALERTAS": "Concluída com alertas", "FALHOU": "Falhou"}
CATEGORIAS_NC = ["Amostra hemolisada", "Amostra insuficiente", "Identificação divergente",
                 "Transporte fora da temperatura", "Requisição incompleta", "Falta de reagente",
                 "Equipamento parado", "Controle de qualidade fora do limite", "Atraso na liberação de laudo",
                 "Laudo com erro de digitação", "Cadastro incorreto no agendamento",
                 "Paciente atendido sem agendamento", "Outra"]

TODOS = {"etapa": ETAPA, "situacao_triagem": SITUACAO_TRIAGEM, "prioridade_triagem": PRIORIDADE_TRIAGEM,
         "status_nc": STATUS_NC, "prioridade_nc": PRIORIDADE_NC, "status_plano": STATUS_PLANO,
         "perfil": PERFIL, "status_importacao": STATUS_IMPORTACAO}


def rotulo(dominio: str, valor) -> str:
    return TODOS.get(dominio, {}).get(valor, valor or "—")
