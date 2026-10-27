-- =====================================================================
-- LACEN Intelligence — 003_seguranca.sql
-- 1. Row Level Security ligado em TODAS as tabelas do schema public.
--    Sem políticas para anon/authenticated => a API REST pública do
--    Supabase não lê nem grava nada. Só o backend (conexão de servidor)
--    acessa os dados.
-- 2. Papel somente-leitura para o Power BI, restrito ao schema analitico.
-- =====================================================================

BEGIN;

DO $$
DECLARE t TEXT;
BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        -- ENABLE (sem FORCE): o dono das tabelas — que também é dono das views
        -- analíticas — continua lendo; todos os outros papéis dependem de política.
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    END LOOP;

    -- No Supabase os papéis anon/authenticated existem: removemos qualquer privilégio direto.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon';
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA analitico FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated';
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA analitico FROM authenticated';
    END IF;

    -- Papel da aplicação: dono lógico dos dados, ignora RLS por política explícita.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lacen_app') THEN
        CREATE ROLE lacen_app NOLOGIN;
    END IF;
    -- Papel do Power BI: lê apenas o schema analitico.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lacen_bi') THEN
        CREATE ROLE lacen_bi NOLOGIN;
    END IF;
END $$;

-- Política "tudo liberado" apenas para o papel da aplicação
DO $$
DECLARE t TEXT;
BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('DROP POLICY IF EXISTS app_total ON public.%I', t);
        EXECUTE format('CREATE POLICY app_total ON public.%I FOR ALL TO lacen_app USING (true) WITH CHECK (true)', t);
    END LOOP;
END $$;

GRANT USAGE ON SCHEMA public, analitico TO lacen_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO lacen_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lacen_app;
GRANT SELECT ON ALL TABLES IN SCHEMA analitico TO lacen_app;

GRANT USAGE ON SCHEMA analitico TO lacen_bi;
GRANT SELECT ON ALL TABLES IN SCHEMA analitico TO lacen_bi;
REVOKE ALL ON SCHEMA public FROM lacen_bi;

-- As views do schema analitico rodam com o privilégio do dono (que lê as tabelas),
-- por isso o lacen_bi enxerga os agregados sem enxergar a tabela pacientes.

COMMIT;

-- Para criar o login do Power BI (rode manualmente, com senha forte):
--   CREATE ROLE powerbi LOGIN PASSWORD '<senha-forte>' IN ROLE lacen_bi;
-- Para o usuário de conexão da aplicação:
--   CREATE ROLE app_lacen LOGIN PASSWORD '<senha-forte>' IN ROLE lacen_app;
