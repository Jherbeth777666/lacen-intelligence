# LACEN Intelligence — estudo de caso

> Desenvolvi uma plataforma de inteligência gerencial para laboratório. Ela integra os dados de
> agendamento das UBS com a produção laboratorial do Worklab, usando tratamento de dados em
> Python/Pandas, PostgreSQL/Supabase, Power BI e IA para análise e apoio à decisão.

## Problema
O Laboratório Central recebe pacientes agendados por cerca de 20 unidades de saúde. Antes do projeto:
- **Agendamento e produção viviam em sistemas separados.** A planilha da UBS e o Worklab não se
  cruzavam, então ninguém sabia quantos agendados chegavam de fato ao laboratório.
- **O absenteísmo era alto e invisível.** Ninguém sabia quais unidades e dias concentravam as faltas.
- **Quase todos os pacientes eram marcados no mesmo horário de abertura**, o que formava fila na chegada.
- **A triagem era manual**: dados redigitados, sem registro de quem confirmou nem quando.
- **Os indicadores eram montados à mão**, mensalmente, em planilhas locais com dados pessoais soltos,
  incluindo campo de observação com informação clínica.

## Solução
Uma plataforma web com fluxo completo:
**triagem → importação → tratamento → armazenamento → indicadores → alertas → pergunta à IA → decisão → plano 5W2H.**

| Módulo | O que entrega |
|---|---|
| Triagem | Cadastro com validação de CPF, exames do catálogo, fila priorizada, confirmação auditada |
| Importação (ETL) | Upload de CSV/Excel do Worklab e das agendas. Valida, padroniza, deduplica e pseudonimiza, com relatório de erros por linha |
| Dashboard | KPIs executivos com variação vs. mês anterior, alertas, rankings e tendências |
| Agendamentos | Faltas por UBS, dia da semana e horário; cruzamento agenda × laboratório |
| Laboratório | Produção por setor, TAT, % no prazo, recoletas, exames em alta/queda |
| Qualidade | Não conformidades por etapa, reincidência, prazos, plano 5W2H |
| Processos | BPMN AS-IS e TO-BE ligados a evidências medidas pelo sistema |
| Analista IA | Perguntas em linguagem natural respondidas só com dados da base, citando o período e os registros analisados |

## Processo
**Antes (AS-IS):** agenda em planilha com horário único → conferência e redigitação do cadastro →
triagem manual → coleta → processamento → liberação → exportação manual mensal do Worklab para
planilhas.

**Depois (TO-BE):** agenda em blocos de 30 min → lembrete automático → triagem no sistema, com
pré-cadastro vindo da agenda → coleta com checklist → alerta de TAT → ETL diário → indicadores,
alertas e IA → plano de ação 5W2H.

Gargalos mapeados (com evidência medida): ponto de espera na chegada, vagas ociosas, redigitação,
recoleta, atraso de liberação, falta de integração entre os sistemas e NCs sem tratamento no prazo.

## Dados
- **Agendamentos** das UBS (data, horário, unidade, paciente, tipo, status)
- **Worklab**: atendimento, data, paciente, exame, valor, situação, liberação
- **Triagem** (gerada no sistema) e **não conformidades** (registradas no sistema)
- Os pacientes viram um pseudônimo HMAC **consistente entre fontes**, e é isso que permite o cruzamento
  agendamento × comparecimento × produção sem expor identidade.
- O sistema traz uma base de demonstração **100% fictícia**, carregada pelo mesmo ETL dos dados reais.

## Tecnologia
Python 3 · Flask · SQLAlchemy Core · Pandas · PostgreSQL 16 / Supabase · Chart.js · bpmn-js ·
Power BI · Claude API (tool use) · pytest · Docker.

Decisões principais:
- **SQL explícito, sem ORM.** Indicadores legíveis, auditáveis e reaproveitados no Power BI.
- **Camada `analitico` em views.** Um só lugar para as regras de negócio, sem dados pessoais.
- **Flask com renderização no servidor.** Simples e rápido de manter, sem build de frontend.

## BI
Cerca de 20 indicadores com definição documentada ([INDICADORES.md](INDICADORES.md)). O Power BI usa o
mesmo modelo estrela e as mesmas fórmulas ([POWERBI.md](POWERBI.md)), com 5 páginas: Executiva, UBS,
Exames, Gestão e Qualidade.

## IA
- A IA **não escreve SQL e não vê dados pessoais**. Ela só chama 12 ferramentas de um catálogo fechado
  (KPIs, ranking de UBS, faltas por dia, produção por setor, NCs, alertas...).
- **Quem detecta problemas são as regras estatísticas** (teste z de proporções); a IA explica e
  prioriza. Assim ela não "descobre" tendência em ruído.
- Toda resposta traz a **base analisada** (período, UBS, nº de registros), calculada pelo sistema e
  não pelo modelo.
- Quando faltam dados, a resposta é **"Não há dados suficientes para concluir."** Causas aparecem
  sempre como hipóteses a verificar.
- Recomendações seguem a estrutura **Problema → Evidência → Possível causa → Ação → Indicador**.
- Sem chave de API, um **modo analítico offline** responde com a mesma estrutura, então a demonstração
  nunca quebra.

## Resultado — decisões que ficam melhores
- **Onde agir no absenteísmo:** a UBS e o dia da semana certos, com significância estatística, no
  lugar de uma impressão geral.
- **Agenda escalonada:** a concentração às 07:00 deixou de ser percepção e virou número.
- **Insumos e capacidade:** a queda de % no prazo de um setor aparece junto com as NCs de falta de
  reagente do mesmo período.
- **Transporte e coleta:** a recoleta por UBS mostra onde auditar.
- **Integração de sistemas:** o % de atendimentos sem agendamento mede a qualidade da integração.
- **Governança:** cada importação, alteração e pergunta à IA fica registrada.
