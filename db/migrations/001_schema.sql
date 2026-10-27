-- =====================================================================
-- LACEN Intelligence — 001_schema.sql
-- Modelo de dados operacional + fatos importados + qualidade + governança
-- Compatível com PostgreSQL 14+ e Supabase.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- CADASTROS (dimensões)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ubs (
    id          SERIAL PRIMARY KEY,
    nome        TEXT NOT NULL UNIQUE,
    tipo        TEXT NOT NULL DEFAULT 'UBS'
                CHECK (tipo IN ('UBS','CENTRO_ESPECIALIDADES','CTA','ESPACO','PRISIONAL','OUTRO')),
    ativo       BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Grafias alternativas de uma UBS (resolve "UBS escrita de formas diferentes")
CREATE TABLE IF NOT EXISTS ubs_alias (
    alias       TEXT PRIMARY KEY,          -- sempre normalizado (maiúsculo, sem acento, espaço simples)
    ubs_id      INT NOT NULL REFERENCES ubs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS setores (
    id          SERIAL PRIMARY KEY,
    nome        TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS exames (
    id          SERIAL PRIMARY KEY,
    sigla       TEXT NOT NULL UNIQUE,
    nome        TEXT NOT NULL,
    setor_id    INT REFERENCES setores(id),
    valor_ref   NUMERIC(10,2),             -- valor de referência ilustrativo
    prazo_horas INT,                       -- prazo-meta de liberação (TAT)
    ativo       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS exame_alias (
    alias       TEXT PRIMARY KEY,
    exame_id    INT NOT NULL REFERENCES exames(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS profissionais (
    id          SERIAL PRIMARY KEY,
    nome        TEXT NOT NULL,
    conselho    TEXT NOT NULL DEFAULT 'CRM',
    registro    TEXT NOT NULL,
    uf          CHAR(2) NOT NULL DEFAULT 'MA',
    ativo       BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (conselho, registro, uf)
);

CREATE TABLE IF NOT EXISTS usuarios (
    id            SERIAL PRIMARY KEY,
    nome          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    senha_hash    TEXT NOT NULL,
    perfil        TEXT NOT NULL DEFAULT 'triagem'
                  CHECK (perfil IN ('admin','gestor','triagem','qualidade')),
    ativo         BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ultimo_login  TIMESTAMPTZ
);

-- ---------------------------------------------------------------------
-- OPERACIONAL (contém dados pessoais — acesso restrito)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pacientes (
    id               SERIAL PRIMARY KEY,
    pseudo_id        TEXT NOT NULL UNIQUE,          -- PAC_xxxxxxxxxx (HMAC) — único identificador que sai daqui
    nome             TEXT NOT NULL,
    cpf              CHAR(11) UNIQUE,               -- só dígitos
    cns              CHAR(15) UNIQUE,               -- Cartão Nacional de Saúde
    data_nascimento  DATE NOT NULL CHECK (data_nascimento > DATE '1900-01-01'),
    sexo             CHAR(1) NOT NULL CHECK (sexo IN ('M','F')),
    endereco         TEXT,
    ubs_id           INT REFERENCES ubs(id),
    criado_em        TIMESTAMPTZ NOT NULL DEFAULT now(),
    criado_por       INT REFERENCES usuarios(id)
);

CREATE TABLE IF NOT EXISTS triagens (
    id               SERIAL PRIMARY KEY,
    paciente_id      INT NOT NULL REFERENCES pacientes(id),
    ubs_id           INT NOT NULL REFERENCES ubs(id),
    profissional_id  INT REFERENCES profissionais(id),
    data_triagem     TIMESTAMPTZ NOT NULL DEFAULT now(),
    prioridade       TEXT NOT NULL DEFAULT 'ROTINA'
                     CHECK (prioridade IN ('ROTINA','PRIORITARIA','URGENTE')),
    situacao         TEXT NOT NULL DEFAULT 'AGUARDANDO'
                     CHECK (situacao IN ('AGUARDANDO','CONFIRMADA','COLETADA','CANCELADA')),
    observacoes      TEXT,
    responsavel_id   INT REFERENCES usuarios(id),    -- quem registrou
    confirmado_em    TIMESTAMPTZ,
    confirmado_por   INT REFERENCES usuarios(id)
);

-- Um exame por linha (substitui as colunas exame_codigo/codigo_exame/examen_codigo do projeto antigo)
CREATE TABLE IF NOT EXISTS triagem_exames (
    triagem_id  INT NOT NULL REFERENCES triagens(id) ON DELETE CASCADE,
    exame_id    INT NOT NULL REFERENCES exames(id),
    PRIMARY KEY (triagem_id, exame_id)
);

-- ---------------------------------------------------------------------
-- GOVERNANÇA DE IMPORTAÇÃO
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS importacoes (
    id              SERIAL PRIMARY KEY,
    fonte           TEXT NOT NULL CHECK (fonte IN ('AGENDAMENTOS','WORKLAB')),
    arquivo         TEXT NOT NULL,
    hash_arquivo    CHAR(64) NOT NULL UNIQUE,      -- impede importar o mesmo arquivo duas vezes
    usuario_id      INT REFERENCES usuarios(id),
    iniciado_em     TIMESTAMPTZ NOT NULL DEFAULT now(),
    concluido_em    TIMESTAMPTZ,
    linhas_lidas    INT NOT NULL DEFAULT 0,
    inseridas       INT NOT NULL DEFAULT 0,
    duplicadas      INT NOT NULL DEFAULT 0,
    rejeitadas      INT NOT NULL DEFAULT 0,
    periodo_inicio  DATE,
    periodo_fim     DATE,
    status          TEXT NOT NULL DEFAULT 'PROCESSANDO'
                    CHECK (status IN ('PROCESSANDO','CONCLUIDA','CONCLUIDA_COM_ALERTAS','FALHOU'))
);

CREATE TABLE IF NOT EXISTS importacao_erros (
    id              BIGSERIAL PRIMARY KEY,
    importacao_id   INT NOT NULL REFERENCES importacoes(id) ON DELETE CASCADE,
    linha           INT,
    campo           TEXT,
    valor           TEXT,          -- valor mascarado quando for dado pessoal
    problema        TEXT NOT NULL,
    severidade      TEXT NOT NULL DEFAULT 'REJEITADA' CHECK (severidade IN ('REJEITADA','AVISO'))
);

-- ---------------------------------------------------------------------
-- FATOS IMPORTADOS (só pseudônimos — nunca nome/CPF/CNS)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agendamentos (
    id               BIGSERIAL PRIMARY KEY,
    data             DATE NOT NULL,
    horario          TIME,
    ubs_id           INT NOT NULL REFERENCES ubs(id),
    paciente_pseudo  TEXT NOT NULL,
    tipo             TEXT NOT NULL DEFAULT 'ROTINA' CHECK (tipo IN ('ROTINA','URGENCIA')),
    status           TEXT NOT NULL CHECK (status IN ('AGENDADO','COMPARECEU','FALTOU','PENDENTE')),
    importacao_id    INT REFERENCES importacoes(id) ON DELETE SET NULL,
    UNIQUE (data, ubs_id, paciente_pseudo)          -- regra de deduplicação
);

CREATE TABLE IF NOT EXISTS atendimentos (
    id               BIGSERIAL PRIMARY KEY,
    codigo_worklab   TEXT NOT NULL UNIQUE,
    data             DATE NOT NULL,
    paciente_pseudo  TEXT NOT NULL,
    ubs_id           INT REFERENCES ubs(id),        -- pode vir vazio no Worklab; inferido na camada analítica
    convenio         TEXT NOT NULL DEFAULT 'SUS',
    importacao_id    INT REFERENCES importacoes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS atendimento_exames (
    id               BIGSERIAL PRIMARY KEY,
    atendimento_id   BIGINT NOT NULL REFERENCES atendimentos(id) ON DELETE CASCADE,
    exame_id         INT NOT NULL REFERENCES exames(id),
    quantidade       INT NOT NULL DEFAULT 1 CHECK (quantidade > 0),
    valor            NUMERIC(10,2) CHECK (valor >= 0),
    situacao         TEXT NOT NULL DEFAULT 'LIBERADO'
                     CHECK (situacao IN ('COLETADO','EM_ANALISE','LIBERADO','RECOLETA','CANCELADO')),
    liberado_em      TIMESTAMPTZ,
    UNIQUE (atendimento_id, exame_id)
);

-- ---------------------------------------------------------------------
-- QUALIDADE
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nao_conformidades (
    id               SERIAL PRIMARY KEY,
    data             DATE NOT NULL DEFAULT CURRENT_DATE,
    setor_id         INT REFERENCES setores(id),
    ubs_id           INT REFERENCES ubs(id),
    etapa            TEXT NOT NULL CHECK (etapa IN ('PRE_ANALITICA','ANALITICA','POS_ANALITICA','ADMINISTRATIVA')),
    categoria        TEXT NOT NULL,
    problema         TEXT NOT NULL,
    causa            TEXT,
    responsavel_id   INT REFERENCES usuarios(id),
    prioridade       TEXT NOT NULL DEFAULT 'MEDIA' CHECK (prioridade IN ('BAIXA','MEDIA','ALTA','CRITICA')),
    prazo            DATE,
    status           TEXT NOT NULL DEFAULT 'ABERTA'
                     CHECK (status IN ('ABERTA','EM_TRATAMENTO','CONCLUIDA','CANCELADA')),
    acao_corretiva   TEXT,
    observacao       TEXT,
    concluida_em     DATE,
    criado_por       INT REFERENCES usuarios(id),
    criado_em        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (concluida_em IS NULL OR concluida_em >= data)
);

-- Plano de ação 5W2H vinculado a uma não conformidade
CREATE TABLE IF NOT EXISTS planos_acao (
    id               SERIAL PRIMARY KEY,
    nc_id            INT NOT NULL REFERENCES nao_conformidades(id) ON DELETE CASCADE,
    o_que            TEXT NOT NULL,    -- What
    por_que          TEXT NOT NULL,    -- Why
    onde             TEXT,             -- Where
    quando           DATE,             -- When
    quem             TEXT,             -- Who
    como             TEXT,             -- How
    quanto           TEXT,             -- How much
    status           TEXT NOT NULL DEFAULT 'PLANEJADA'
                     CHECK (status IN ('PLANEJADA','EM_ANDAMENTO','CONCLUIDA','ATRASADA')),
    criado_em        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- AUDITORIA E IA
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auditoria (
    id           BIGSERIAL PRIMARY KEY,
    quando       TIMESTAMPTZ NOT NULL DEFAULT now(),
    usuario_id   INT,
    tabela       TEXT NOT NULL,
    registro_id  TEXT,
    acao         TEXT NOT NULL CHECK (acao IN ('INSERT','UPDATE','DELETE')),
    antes        JSONB,
    depois       JSONB
);

CREATE TABLE IF NOT EXISTS ia_consultas (
    id            BIGSERIAL PRIMARY KEY,
    quando        TIMESTAMPTZ NOT NULL DEFAULT now(),
    usuario_id    INT REFERENCES usuarios(id),
    pergunta      TEXT NOT NULL,
    modo          TEXT NOT NULL CHECK (modo IN ('LLM','OFFLINE')),
    ferramentas   JSONB,
    resposta      TEXT,
    base          JSONB
);

-- ---------------------------------------------------------------------
-- ÍNDICES
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_agend_data        ON agendamentos (data);
CREATE INDEX IF NOT EXISTS ix_agend_ubs_data    ON agendamentos (ubs_id, data);
CREATE INDEX IF NOT EXISTS ix_agend_pac         ON agendamentos (paciente_pseudo, data);
CREATE INDEX IF NOT EXISTS ix_atend_data        ON atendimentos (data);
CREATE INDEX IF NOT EXISTS ix_atend_pac         ON atendimentos (paciente_pseudo, data);
CREATE INDEX IF NOT EXISTS ix_atex_atend        ON atendimento_exames (atendimento_id);
CREATE INDEX IF NOT EXISTS ix_atex_exame        ON atendimento_exames (exame_id);
CREATE INDEX IF NOT EXISTS ix_triagem_situacao  ON triagens (situacao, data_triagem);
CREATE INDEX IF NOT EXISTS ix_nc_status         ON nao_conformidades (status, data);
CREATE INDEX IF NOT EXISTS ix_auditoria_tabela  ON auditoria (tabela, registro_id);

-- ---------------------------------------------------------------------
-- TRIGGER DE AUDITORIA
-- O usuário da aplicação é passado via: SET LOCAL app.usuario_id = '<id>'
-- Campos sensíveis são removidos do JSON antes de gravar.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_auditoria() RETURNS trigger AS $$
DECLARE
    v_usuario INT := NULLIF(current_setting('app.usuario_id', TRUE), '')::INT;
    v_antes   JSONB;
    v_depois  JSONB;
BEGIN
    IF TG_OP IN ('UPDATE','DELETE') THEN
        v_antes := to_jsonb(OLD) - 'senha_hash' - 'cpf' - 'cns' - 'endereco';
    END IF;
    IF TG_OP IN ('INSERT','UPDATE') THEN
        v_depois := to_jsonb(NEW) - 'senha_hash' - 'cpf' - 'cns' - 'endereco';
    END IF;
    INSERT INTO auditoria (usuario_id, tabela, registro_id, acao, antes, depois)
    VALUES (v_usuario, TG_TABLE_NAME,
            COALESCE(v_depois->>'id', v_antes->>'id'),
            TG_OP, v_antes, v_depois);
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['pacientes','triagens','nao_conformidades','planos_acao','usuarios','exames','ubs']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_auditoria ON %I', t);
        EXECUTE format('CREATE TRIGGER trg_auditoria AFTER INSERT OR UPDATE OR DELETE ON %I
                        FOR EACH ROW EXECUTE FUNCTION fn_auditoria()', t);
    END LOOP;
END $$;

COMMIT;
