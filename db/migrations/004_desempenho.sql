-- =====================================================================
-- LACEN Intelligence — 004_desempenho.sql
-- As três fatos mais pesadas viram VIEWS MATERIALIZADAS: o cruzamento
-- agendamento × atendimento e a inferência de UBS passam a ser calculados
-- uma vez por importação, e não a cada consulta do painel.
-- Atualização: analitico.atualizar() — chamada automaticamente ao fim de
-- cada importação (etl/importador.py). Depois de mexer no banco à mão:
--   SELECT analitico.atualizar();
-- =====================================================================

BEGIN;

DROP VIEW IF EXISTS analitico.kpi_ubs_mes;
DROP VIEW IF EXISTS analitico.fato_exames;
DROP VIEW IF EXISTS analitico.fato_atendimentos;
DROP VIEW IF EXISTS analitico.fato_agendamentos;

CREATE MATERIALIZED VIEW analitico.fato_agendamentos AS
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

CREATE UNIQUE INDEX ON analitico.fato_agendamentos (agendamento_id);
CREATE INDEX ON analitico.fato_agendamentos (data);
CREATE INDEX ON analitico.fato_agendamentos (ubs_id, data);

CREATE MATERIALIZED VIEW analitico.fato_atendimentos AS
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

CREATE UNIQUE INDEX ON analitico.fato_atendimentos (atendimento_id);
CREATE INDEX ON analitico.fato_atendimentos (data);
CREATE INDEX ON analitico.fato_atendimentos (ubs_id, data);

CREATE MATERIALIZED VIEW analitico.fato_exames AS
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

CREATE UNIQUE INDEX ON analitico.fato_exames (atendimento_exame_id);
CREATE INDEX ON analitico.fato_exames (data);
CREATE INDEX ON analitico.fato_exames (exame_id);

CREATE VIEW analitico.kpi_ubs_mes AS
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

CREATE OR REPLACE FUNCTION analitico.atualizar() RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    REFRESH MATERIALIZED VIEW analitico.fato_agendamentos;
    REFRESH MATERIALIZED VIEW analitico.fato_atendimentos;
    REFRESH MATERIALIZED VIEW analitico.fato_exames;      -- depende de fato_atendimentos
END $$;

-- mesmas permissões das outras views da camada analítica
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lacen_app') THEN
        EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA analitico TO lacen_app';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lacen_bi') THEN
        EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA analitico TO lacen_bi';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA analitico FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA analitico FROM authenticated';
    END IF;
END $$;

COMMIT;
