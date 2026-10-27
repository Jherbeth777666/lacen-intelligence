# Power BI — painel LACEN Intelligence

O painel está pronto em **`powerbi/LACEN_Intelligence.pbip`** (Projeto do Power BI: modelo em TMDL e
relatório em PBIR, formatos de texto oficiais da Microsoft). Ele lê **somente a camada analítica**, que não
tem nome, CPF, CNS nem endereço. As medidas usam as mesmas definições do site e do Analista IA.

## 1. Abrir (passo a passo)

1. Gere os dados que o painel lê:
   ```
   python -m scripts.exportar_powerbi
   ```
   Cria `data/powerbi/*.csv` a partir do banco configurado no `.env`.
2. (Só se você mudou a pasta do projeto de lugar) gere o projeto de novo, para ele apontar para a pasta certa:
   ```
   python -m powerbi.gerar_projeto
   ```
3. Abra `powerbi/LACEN_Intelligence.pbip` no **Power BI Desktop atualizado** (versão de setembro de 2026 ou
   mais nova, em que PBIP/PBIR/TMDL são recursos gerais).
4. Clique em **Atualizar**. Se pedir, em *Transformar dados → Editar parâmetros*, confira `PastaDados`.
5. Para ter um `.pbix`: **Arquivo → Salvar como → Arquivo do Power BI (.pbix)**.

Para atualizar com dados novos: rode de novo o passo 1 e clique em **Atualizar** no Power BI.

## 2. Modelo (estrela, relações 1:N com filtro em direção única)

| Tabela | Arquivo | Conteúdo |
|---|---|---|
| Calendário | `dim_calendario.csv` | Uma linha por dia. Tabela de datas do modelo. |
| UBS | `dim_ubs.csv` | Unidades de saúde (UBS e outros pontos de coleta). |
| Exame | `dim_exame.csv` | Catálogo de exames com setor e prazo de liberação. |
| Agendamentos | `fato_agendamentos.csv` | Agendamentos das UBS. 'realizado' = compareceu ou faltou (base da taxa de faltas). |
| Atendimentos | `fato_atendimentos.csv` | Atendimentos do Worklab. UBS informada ou inferida pelo último agendamento (até 30 dias). |
| Exames Realizados | `fato_exames.csv` | Um exame por linha. TAT = liberação − (data do atendimento às 07:00). |
| Não Conformidades | `fato_nao_conformidades.csv` | Não conformidades registradas no módulo de qualidade. |
| Triagens | `fato_triagens.csv` | Triagens (sem identificação do paciente). |
| Medidas | — | Só as medidas (tabela sem dados). |

Relações: Agendamentos[data] → Calendário[Data], Atendimentos[data] → Calendário[Data], Exames Realizados[data] → Calendário[Data], Não Conformidades[data] → Calendário[Data], Triagens[data] → Calendário[Data], Agendamentos[ubs_id] → UBS[ubs_id], Atendimentos[ubs_id] → UBS[ubs_id], Exames Realizados[ubs_id] → UBS[ubs_id], Não Conformidades[ubs_id] → UBS[ubs_id], Triagens[ubs_id] → UBS[ubs_id], Exames Realizados[exame_id] → Exame[exame_id].

- `Calendário` está marcada como tabela de datas; a *data/hora automática* está desligada.
- O cruzamento agendamento × laboratório vem pronto do banco (`Atendido no Lab`, `Tinha Agendamento`).
  Não existe relação por paciente (o painel antigo ligava por nome, em M:M bidirecional).
- Chaves e `paciente_pseudo` ficam ocultos.

## 3. Medidas

**Agendamento**

- `Total de Agendamentos` = `COUNTROWS ( Agendamentos )` — Total de agendamentos (inclui pendentes).
- `Agendamentos Realizados` = `SUM ( Agendamentos[realizado] )` — Compareceram + faltaram. Base da taxa de faltas.
- `Comparecimentos` = `SUM ( Agendamentos[compareceu] )` — Agendamentos com status COMPARECEU.
- `Faltas` = `SUM ( Agendamentos[faltou] )` — Agendamentos com status FALTOU.
- `Pendentes` = `CALCULATE ( [Total de Agendamentos], Agendamentos[Status] = "PENDENTE" )` — Agendamentos ainda sem desfecho registrado.
- `Taxa de Faltas` = `DIVIDE ( [Faltas], [Agendamentos Realizados] )` — Faltas ÷ (compareceram + faltaram). Pendentes e agenda futura ficam fora.
- `Taxa de Comparecimento` = `DIVIDE ( [Comparecimentos], [Agendamentos Realizados] )` — Comparecimentos ÷ agendamentos realizados.
- `Taxa de Faltas da Rede` = `CALCULATE ( [Taxa de Faltas], REMOVEFILTERS ( UBS ) )` — Taxa de faltas de todas as UBS juntas, no período filtrado.
- `Faltas vs Rede` = `DIVIDE ( [Taxa de Faltas], [Taxa de Faltas da Rede] )` — Quantas vezes a taxa da UBS está acima (>1) ou abaixo (<1) da rede.
- `Taxa de Faltas (mês anterior)` = `IF ( HASONEVALUE ( 'Calendário'[Ano-Mês] ), CALCULATE ( [Taxa de Faltas], DATEADD ( 'Calendário'[Data], -1, MONTH ) ) )` — Taxa de faltas no mês anterior. Só aparece com um único mês selecionado.
- `Δ Taxa de Faltas (p.p.)` = `IF ( NOT ISBLANK ( [Taxa de Faltas] ) && NOT ISBLANK ( [Taxa de Faltas (mês anterior)] ), ( [Taxa de Faltas] - [Taxa de Faltas (mês anterior)] ) * 100 )` — Variação em pontos percentuais em relação ao mês anterior.
- `Pacientes Agendados` = `DISTINCTCOUNT ( Agendamentos[paciente_pseudo] )` — Pacientes distintos (pseudônimo) com agendamento.

**Cruzamento**

- `Comparecidos Atendidos no Lab %` = `DIVIDE ( CALCULATE ( [Comparecimentos], Agendamentos[Atendido no Lab] = TRUE () ), [Comparecimentos] )` — Dos que constam como 'compareceu', quantos têm atendimento no laboratório em até 7 dias.
- `Faltosos Atendidos no Lab %` = `DIVIDE ( CALCULATE ( [Faltas], Agendamentos[Atendido no Lab] = TRUE () ), [Faltas] )` — Dos que constam como 'faltou', quantos foram atendidos no laboratório em até 7 dias (status a revisar).
- `Atendimentos sem Agendamento %` = `DIVIDE ( CALCULATE ( [Total de Atendimentos], Atendimentos[Tinha Agendamento] = FALSE () ), [Total de Atendimentos] )` — Atendimentos do laboratório sem agendamento do paciente nos 30 dias anteriores.

**Produção**

- `Total de Atendimentos` = `COUNTROWS ( Atendimentos )` — Atendimentos no laboratório.
- `Pacientes Atendidos` = `DISTINCTCOUNT ( Atendimentos[paciente_pseudo] )` — Pacientes distintos (pseudônimo) atendidos.
- `Total de Exames` = `SUM ( 'Exames Realizados'[quantidade] )` — Exames realizados.
- `Exames por Atendimento` = `DIVIDE ( [Total de Exames], DISTINCTCOUNT ( 'Exames Realizados'[atendimento_id] ) )` — Média de exames por atendimento (conta só atendimentos com exames no filtro).
- `Atendimentos por Dia` = `AVERAGEX ( VALUES ( 'Calendário'[Data] ), [Total de Atendimentos] )` — Média de atendimentos nos dias com atendimento.
- `Exames (mês anterior)` = `IF ( HASONEVALUE ( 'Calendário'[Ano-Mês] ), CALCULATE ( [Total de Exames], DATEADD ( 'Calendário'[Data], -1, MONTH ) ) )` — Exames no mês anterior. Só aparece com um único mês selecionado.
- `Variação de Exames %` = `IF ( HASONEVALUE ( 'Calendário'[Ano-Mês] ), DIVIDE ( [Total de Exames] - [Exames (mês anterior)], [Exames (mês anterior)] ) )` — Variação percentual de exames em relação ao mês anterior.
- `Valor de Referência (R$)` = `SUM ( 'Exames Realizados'[valor] )` — Soma do valor de referência dos exames (ilustrativo).

**Prazo**

- `TAT Médio (h)` = `AVERAGE ( 'Exames Realizados'[tat_horas] )` — Tempo médio de liberação em horas.
- `% Liberado no Prazo` = `DIVIDE ( CALCULATE ( COUNTROWS ( 'Exames Realizados' ), 'Exames Realizados'[Dentro do Prazo] = TRUE () ), CALCULATE ( COUNTROWS ( 'Exames Realizados' ), NOT ISBLANK ( 'Exames Realizados'[Dentro do Prazo] ) ) )` — Exames liberados dentro do prazo do catálogo ÷ exames liberados.
- `Taxa de Recoleta` = `DIVIDE ( CALCULATE ( COUNTROWS ( 'Exames Realizados' ), 'Exames Realizados'[Situação] = "RECOLETA" ), COUNTROWS ( 'Exames Realizados' ) )` — Exames com situação RECOLETA ÷ exames.

**Qualidade**

- `NCs` = `COUNTROWS ( 'Não Conformidades' )` — Não conformidades registradas.
- `NCs Abertas` = `CALCULATE ( [NCs], 'Não Conformidades'[Status] IN { "ABERTA", "EM_TRATAMENTO" } )` — Não conformidades abertas ou em tratamento.
- `NCs Vencidas` = `CALCULATE ( [NCs], 'Não Conformidades'[Vencida] = TRUE () )` — Abertas com prazo vencido.
- `Tempo Médio de Resolução (dias)` = `AVERAGE ( 'Não Conformidades'[dias_resolucao] )` — Dias entre o registro e a conclusão.
- `% Ações 5W2H Concluídas` = `DIVIDE ( SUM ( 'Não Conformidades'[acoes_concluidas] ), SUM ( 'Não Conformidades'[qtd_acoes] ) )` — Ações 5W2H concluídas ÷ ações cadastradas.

**Triagem**

- `Total de Triagens` = `COUNTROWS ( Triagens )` — Triagens registradas.
- `Triagens Aguardando` = `CALCULATE ( [Total de Triagens], Triagens[Situação] = "AGUARDANDO" )` — Triagens ainda não confirmadas.

Comparações com o mês anterior só aparecem com **um único mês** selecionado na segmentação.
Registros sem UBS aparecem como **"Não identificada"**.

## 4. Páginas

| Página | Visuais | Pergunta gerencial |
|---|---|---|
| **Visão Executiva** | Cartões (agendamentos, taxa de faltas, atendimentos, exames, pacientes, sem agendamento) · atendimentos × taxa de faltas por mês · taxa de faltas por UBS · atendimentos por dia | Como estamos? |
| **UBS** | Taxa de faltas por UBS · tabela com Δ p.p. e comparação com a rede · matriz UBS × dia da semana | Qual UBS precisa de atenção? |
| **Exames** | Exames por sigla · por setor · por mês · tabela com variação vs. mês anterior | O que mudou na produção? |
| **Gestão** | Cartões de cruzamento · TAT por setor · % no prazo por mês · faltas por dia da semana · recoleta por UBS | Onde estão os gargalos? |
| **Qualidade** | Cartões de NC · NC por mês e etapa · por categoria · tabela de situação e prazo | Os problemas estão sendo tratados? |

Segmentações de **Ano-Mês** e **UBS** em todas as páginas, sincronizadas.

## 5. Do painel antigo para o novo

| Antes (`Painel_UBS_Worklab.pbix`) | Agora |
|---|---|
| Coluna `Data` como texto → `Dia_Semana` com 60 valores | `Calendário` com data tipada e dia da semana correto |
| `Taxa Absenteísmo` sobre todos os agendamentos (25,4%) | `Taxa de Faltas` sobre os realizados (25,9% nos mesmos dados) |
| `Taxa Comparecimento Efetiva` = pacientes do lab ÷ agendados | `Comparecidos Atendidos no Lab %` (em até 7 dias) |
| Relação M:M bidirecional pelo **nome** do paciente | Cruzamento calculado no banco por pseudônimo; relações 1:N |
| Nomes de pacientes dentro do arquivo | Nenhum dado pessoal no modelo |
| CSVs em `C:\Users\Jhon\projeto_bi\saida_bi\` | Parâmetro `PastaDados` |
| 3 tabelas de data automáticas | 1 `Calendário` em português |

## 6. Alternativa: conexão direta ao banco

Para ler o PostgreSQL/Supabase em vez dos CSV, troque a fonte de cada tabela em *Transformar dados* por
*Banco de dados PostgreSQL* → schema `analitico` (mesmas colunas). Use um login só-leitura:
`CREATE ROLE powerbi LOGIN PASSWORD '<senha-forte>' IN ROLE lacen_bi;`

## 7. Como o projeto foi validado

- Arquivos do relatório validados contra os JSON Schemas oficiais (`microsoft/json-schemas`) e com a CLI
  oficial `@microsoft/powerbi-report-authoring-cli` (`validate`: 0 erros).
- TMDL interpretado sem erros; toda referência DAX e todo campo usado nos visuais existe no modelo
  (`tests/test_powerbi.py`).
- Conversões do Power Query conferidas contra os CSV exportados; as medidas reproduzem os números do site
  (ex.: base de demonstração, taxa de faltas 24,3% nos dois).
