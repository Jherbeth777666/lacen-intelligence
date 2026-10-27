-- =====================================================================
-- LACEN Intelligence — 002_analitico.sql
-- Camada analítica: views SEM dados pessoais, consumidas pelo Power BI,
-- pelo dashboard e pelo Analista IA.
-- =====================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS analitico;

-- ---------------- DIMENSÕES ----------------
CREATE OR REPLACE VIEW analitico.dim_ubs AS
SELECT id AS ubs_id, nome AS ubs, tipo, ativo FROM ubs;

CREATE OR REPLACE VIEW analitico.dim_exame AS
SELECT e.id AS exame_id, e.sigla, e.nome AS exame, COALESCE(s.nome, 'Não classificado') AS setor,
       e.valor_ref, e.prazo_horas
FROM exames e LEFT JOIN setores s ON s.id = e.setor_id;

CREATE OR REPLACE VIEW analitico.dim_calendario AS
WITH limites AS (
    SELECT LEAST((SELECT min(data) FROM agendamentos), (SELECT min(data) FROM atendimentos),
                 (SELECT min(data) FROM nao_conformidades), (SELECT min(data_triagem)::date FROM triagens),
                 CURRENT_DATE) AS ini,
           GREATEST((SELECT max(data) FROM agendamentos), (SELECT max(data) FROM atendimentos),
                    CURRENT_DATE) AS fim
)
SELECT d::date                                   AS data,
       EXTRACT(YEAR FROM d)::int                 AS ano,
       EXTRACT(MONTH FROM d)::int                AS mes,
       to_char(d, 'YYYY-MM')                     AS ano_mes,
       (ARRAY['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'])[EXTRACT(MONTH FROM d)::int] AS mes_nome,
       EXTRACT(ISODOW FROM d)::int               AS num_dia_semana,
       (ARRAY['Segunda','Terça','Quarta','Quinta','Sexta','Sábado','Domingo'])[EXTRACT(ISODOW FROM d)::int] AS dia_semana,
       EXTRACT(ISODOW FROM d) < 6                AS dia_util
FROM limites, generate_series(limites.ini, limites.fim, INTERVAL '1 day') AS d;

-- ---------------- FATOS ----------------

-- Agendamento + se o paciente efetivamente foi atendido no laboratório em até 7 dias
CREATE OR REPLACE VIEW analitico.fato_agendamentos AS
SELECT a.id                         AS agendamento_id,
       a.data,
       a.horario,
       a.ubs_id,
       a.paciente_pseudo,
       a.tipo,
       a.status,
       (a.status = 'COMPARECEU')::int AS compareceu,
       (a.status = 'FALTOU')::int     AS faltou,
       (a.status IN ('COMPARECEU','FALTOU'))::int AS realizado,   -- base da taxa de falta
       EXISTS (SELECT 1 FROM atendimentos t
               WHERE t.paciente_pseudo = a.paciente_pseudo
                 AND t.data BETWEEN a.data AND a.data + 7) AS atendido_no_lab
FROM agendamentos a;

-- Atendimento com UBS informada ou inferida pelo último agendamento do paciente (até 30 dias antes)
CREATE OR REPLACE VIEW analitico.fato_atendimentos AS
SELECT t.id                                   AS atendimento_id,
       t.codigo_worklab,
       t.data,
       t.paciente_pseudo,
       COALESCE(t.ubs_id, inf.ubs_id)         AS ubs_id,
       CASE WHEN t.ubs_id IS NOT NULL THEN 'INFORMADA'
            WHEN inf.ubs_id IS NOT NULL THEN 'INFERIDA'
            ELSE 'NAO_IDENTIFICADA' END       AS origem_ubs,
       t.convenio,
       (inf.ubs_id IS NOT NULL)               AS tinha_agendamento,
       COALESCE(x.qtd_exames, 0)              AS qtd_exames,
       COALESCE(x.valor_total, 0)             AS valor_total
FROM atendimentos t
LEFT JOIN LATERAL (
    SELECT a.ubs_id FROM agendamentos a
    WHERE a.paciente_pseudo = t.paciente_pseudo
      AND a.data BETWEEN t.data - 30 AND t.data
    ORDER BY a.data DESC LIMIT 1
) inf ON TRUE
LEFT JOIN LATERAL (
    SELECT sum(ae.quantidade) AS qtd_exames, sum(ae.valor) AS valor_total
    FROM atendimento_exames ae WHERE ae.atendimento_id = t.id
) x ON TRUE;

-- Exame realizado, com setor e tempo de liberação (TAT).
-- TAT aproximado: liberação − (data do atendimento às 07:00, início da coleta).
CREATE OR REPLACE VIEW analitico.fato_exames AS
SELECT ae.id                    AS atendimento_exame_id,
       fa.atendimento_id,
       fa.data,
       fa.ubs_id,
       e.exame_id,
       e.sigla,
       e.exame,
       e.setor,
       ae.quantidade,
       COALESCE(ae.valor, e.valor_ref * ae.quantidade) AS valor,
       ae.situacao,
       ae.liberado_em,
       CASE WHEN ae.liberado_em IS NOT NULL
            THEN round((EXTRACT(EPOCH FROM ae.liberado_em - (fa.data + TIME '07:00')) / 3600)::numeric, 1)
       END                      AS tat_horas,
       CASE WHEN ae.liberado_em IS NOT NULL AND e.prazo_horas IS NOT NULL
            THEN ae.liberado_em <= (fa.data + TIME '07:00') + make_interval(hours => e.prazo_horas)
       END                      AS dentro_prazo
FROM atendimento_exames ae
JOIN analitico.fato_atendimentos fa ON fa.atendimento_id = ae.atendimento_id
JOIN analitico.dim_exame e          ON e.exame_id = ae.exame_id;

-- Triagens sem identificação do paciente
CREATE OR REPLACE VIEW analitico.fato_triagens AS
SELECT t.id AS triagem_id,
       t.data_triagem::date AS data,
       t.ubs_id,
       t.prioridade,
       t.situacao,
       (SELECT count(*) FROM triagem_exames te WHERE te.triagem_id = t.id) AS qtd_exames,
       round((EXTRACT(EPOCH FROM (COALESCE(t.confirmado_em, now()) - t.data_triagem)) / 3600)::numeric, 1) AS horas_em_aberto
FROM triagens t;

CREATE OR REPLACE VIEW analitico.fato_nao_conformidades AS
SELECT n.id AS nc_id, n.data, n.etapa, n.categoria, n.prioridade, n.status,
       s.nome AS setor, n.ubs_id, n.prazo, n.concluida_em,
       CASE WHEN n.concluida_em IS NOT NULL THEN n.concluida_em - n.data END AS dias_resolucao,
       (n.status IN ('ABERTA','EM_TRATAMENTO') AND n.prazo < CURRENT_DATE) AS vencida,
       (SELECT count(*) FROM planos_acao p WHERE p.nc_id = n.id) AS qtd_acoes,
       (SELECT count(*) FROM planos_acao p WHERE p.nc_id = n.id AND p.status = 'CONCLUIDA') AS acoes_concluidas
FROM nao_conformidades n LEFT JOIN setores s ON s.id = n.setor_id;

-- ---------------- AGREGADO GERENCIAL ----------------
CREATE OR REPLACE VIEW analitico.kpi_ubs_mes AS
WITH ag AS (
    SELECT ubs_id, to_char(data,'YYYY-MM') AS ano_mes,
           count(*) AS agendados, sum(realizado) AS realizados,
           sum(compareceu) AS compareceram, sum(faltou) AS faltas
    FROM analitico.fato_agendamentos GROUP BY 1,2
), at AS (
    SELECT ubs_id, to_char(data,'YYYY-MM') AS ano_mes,
           count(*) AS atendimentos, sum(qtd_exames) AS exames, sum(valor_total) AS valor
    FROM analitico.fato_atendimentos WHERE ubs_id IS NOT NULL GROUP BY 1,2
)
SELECT COALESCE(ag.ubs_id, at.ubs_id) AS ubs_id, u.nome AS ubs,
       COALESCE(ag.ano_mes, at.ano_mes) AS ano_mes,
       COALESCE(ag.agendados,0) AS agendados, COALESCE(ag.compareceram,0) AS compareceram,
       COALESCE(ag.faltas,0) AS faltas,
       -- Taxa de falta = faltas / (compareceram + faltaram); pendentes e agenda futura ficam fora
       round(100.0 * ag.faltas / NULLIF(ag.realizados,0), 1) AS taxa_falta_pct,
       COALESCE(at.atendimentos,0) AS atendimentos, COALESCE(at.exames,0) AS exames,
       COALESCE(at.valor,0) AS valor
FROM ag FULL JOIN at ON at.ubs_id = ag.ubs_id AND at.ano_mes = ag.ano_mes
JOIN ubs u ON u.id = COALESCE(ag.ubs_id, at.ubs_id);

COMMIT;
