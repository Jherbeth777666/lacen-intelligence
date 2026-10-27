# Catálogo de indicadores

Todos os números do sistema (dashboard, telas, alertas, Analista IA) saem de **uma única fonte**:
`app/services/indicadores.py`, que consulta o schema `analitico`.

| Indicador | Fórmula | Para que serve (decisão) |
|---|---|---|
| Pacientes atendidos | pacientes distintos (`paciente_pseudo`) com atendimento no período | Dimensionar a demanda real atendida |
| Atendimentos | nº de atendimentos (códigos Worklab) | Volume de trabalho da recepção/coleta |
| Média de atendimentos por dia | atendimentos ÷ dias com atendimento | Planejar escala e capacidade diária |
| Exames | Σ quantidade de exames | Volume do laboratório, consumo de insumos |
| Exames por atendimento | exames ÷ atendimentos | Mudança no perfil de solicitação |
| Agendamentos | nº de agendamentos com data no período | Demanda planejada pelas UBS |
| **Taxa de faltas** | faltas ÷ (compareceram + faltaram) | Capacidade desperdiçada; onde agir com confirmação ativa |
| Taxa de comparecimento | compareceram ÷ (compareceram + faltaram) | Complemento da taxa de faltas |
| Variação em p.p. | taxa atual − taxa do período anterior | Detectar piora; usada nos alertas com teste z |
| Participação na demanda | agendamentos da UBS ÷ agendamentos da rede | Concentração de demanda e distribuição de vagas |
| Faltas por dia da semana | taxa de faltas agrupada por dia | Ajustar oferta de vagas por dia |
| Concentração por horário | % de agendamentos em cada horário | Evidência do gargalo de chegada às 07:00 |
| Comparecidos atendidos no lab | agendados que compareceram e têm atendimento no Worklab em até 7 dias ÷ compareceram | Qualidade do registro e da integração agenda × laboratório |
| Atendimentos sem agendamento | atendimentos sem agendamento do mesmo paciente nos 30 dias anteriores ÷ atendimentos | Demanda espontânea ou falha de identificação |
| TAT médio / p90 | liberação − (data do atendimento 07:00), em horas | Gargalo do processamento por setor |
| % liberado no prazo | exames liberados até o prazo-meta do exame ÷ exames liberados | Cumprimento do compromisso com a UBS |
| Taxa de recoleta | exames com situação RECOLETA ÷ exames | Retrabalho pré-analítico (coleta/transporte) |
| NCs no período / abertas / vencidas | contagens em `nao_conformidades` | Carga e atraso do sistema de qualidade |
| Tempo médio de resolução | média de (conclusão − abertura) das NCs concluídas no período | Eficiência no tratamento |
| Reincidência | categorias com ≥ 3 NCs nos últimos 90 dias | Problema sistêmico que pede causa raiz |
| % ações 5W2H concluídas | ações concluídas ÷ ações | Execução dos planos de ação |
| Fila de triagem | aguardando, > 24 h, urgentes, minutos até confirmar | Gestão operacional em tempo real |

## Período de comparação

- Mês cheio → compara com o **mês cheio anterior**.
- Intervalo qualquer → compara com o intervalo de mesmo tamanho imediatamente anterior.

## Regras de alerta (`app/services/alertas.py`)

| Alerta | Condição |
|---|---|
| Faltas de uma UBS subiram | ≥ 30 realizados, alta ≥ 8 p.p. (alta severidade ≥ 12 p.p.) **e** teste z de duas proporções significativo a 95% |
| UBS acima da rede | taxa ≥ 1,5× a rede **e** teste z de uma proporção significativo |
| Absenteísmo da rede subiu | + 3 p.p. vs período anterior |
| Concentração de horário | ≥ 80% dos agendamentos num único horário |
| Concentração de demanda | uma UBS com ≥ 12% da demanda |
| Queda de produção | exames por dia útil −15% |
| Gargalo de prazo | setor com < 80% no prazo (alta < 60%) |
| Recoleta | UBS com recoleta ≥ 2% e ≥ 2× a média |
| NCs vencidas / aumento / reincidência | > 0 vencidas · +50% · ≥ 3 em 90 dias |
| Fila de triagem | itens aguardando há mais de 24 h |
| Integração de dados | ≥ 25% dos atendimentos sem agendamento |

O teste estatístico existe para **não transformar ruído em tendência**. Uma UBS com 20 agendamentos
que passa de 25% para 35% de faltas não gera alerta.
