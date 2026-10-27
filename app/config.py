"""Configuração central — tudo vem de variáveis de ambiente (.env)."""
import os
from dotenv import load_dotenv

load_dotenv()


def _obrigatoria(nome: str) -> str:
    valor = os.getenv(nome)
    if not valor:
        raise RuntimeError(f"Variável de ambiente obrigatória ausente: {nome} (veja .env.example)")
    return valor


PRODUCAO = os.getenv("PRODUCAO", "0") == "1"   # 1 = site publicado na internet


class Config:
    PRODUCAO = PRODUCAO
    # Conexão Postgres (local ou Supabase: Project Settings → Database → Connection string)
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/lacen")

    # Em produção a chave é obrigatória (com vários processos do gunicorn, uma chave aleatória derruba o login)
    SECRET_KEY = _obrigatoria("FLASK_SECRET_KEY") if PRODUCAO else (os.getenv("FLASK_SECRET_KEY") or os.urandom(32).hex())
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = PRODUCAO or os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # um turno de trabalho
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024    # uploads de até 20 MB

    # Segredo da pseudonimização (HMAC). NUNCA versionar. Trocar o valor muda todos os PAC_.
    PSEUDO_SECRET = _obrigatoria("PSEUDO_SECRET") if PRODUCAO else os.getenv("PSEUDO_SECRET", "")

    # Analista IA — provedor escolhido pela chave presente (Gemini tem prioridade).
    # Sem nenhuma chave, o Analista funciona no modo analítico offline.
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")

    @classmethod
    def ia(cls) -> dict:
        """Provedor de IA ativo: {'provedor', 'nome', 'chave', 'modelo'} ou provedor 'offline'."""
        if cls.GEMINI_API_KEY:
            return {"provedor": "gemini", "nome": "Gemini (Google)", "chave": cls.GEMINI_API_KEY, "modelo": cls.GEMINI_MODEL}
        if cls.ANTHROPIC_API_KEY:
            return {"provedor": "anthropic", "nome": "Claude (Anthropic)", "chave": cls.ANTHROPIC_API_KEY,
                    "modelo": cls.ANTHROPIC_MODEL}
        return {"provedor": "offline", "nome": "Modo analítico offline", "chave": "", "modelo": ""}

    # Nome mostrado na interface quando a base é de demonstração
    BASE_DEMO = os.getenv("BASE_DEMO", "1") == "1"
