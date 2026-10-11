from datetime import date

from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from pydantic import Field, SecretStr

BASE_DIR = Path(__file__).resolve().parent.parent  # 后端项目的根路径


class Settings(BaseSettings):
    DATABASE_URL: str
    JWT_SECRET_KEY: str
    JWT_EXPIRE_HOURS: int = 24
    JWT_ALGORITHM: str = "HS256"

    # MCP 服务与业务 API 使用同一份数据库和 JWT 配置。
    MCP_HOST: str = "127.0.0.1"
    MCP_PORT: int = 8001
    MCP_PATH: str = "/mcp"

    # DeepSeek OpenAI-compatible API; real key is supplied only by the environment.
    LLM_API_KEY: SecretStr = SecretStr("")
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_MODEL: str = "deepseek-chat"
    LLM_TIMEOUT_SECONDS: float = Field(default=30, gt=0)
    AGENT_MAX_ITERATIONS: int = Field(default=6, ge=1)

    EMBEDDING_API_KEY: SecretStr = SecretStr("")
    EMBEDDING_BASE_URL: str = "https://api.siliconflow.cn/v1"
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    CHROMA_DIR: str = ""
    # Retrieve a wider pool before source/status/distance validation; final output stays at 3.
    RAG_CANDIDATE_COUNT: int = Field(default=8, ge=6, le=8)

    # The semester begins on a Monday. Defaults also apply to existing installs.
    SEMESTER_START_DATE: date = date(2026, 9, 7)
    TOTAL_WEEKS: int = Field(default=20, ge=1)

    # Comma-separated browser origins allowed to call this API. Keep the local
    # Vite defaults; override in .env when serving the frontend from elsewhere.
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8"
    )


settings = Settings()

UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100M
STUDY_PLAN_MAX_LENGTH = 2000

# Relative Chroma paths always resolve against backend, regardless of process cwd.
_chroma_path = Path(settings.CHROMA_DIR).expanduser() if settings.CHROMA_DIR.strip() else BASE_DIR / "chroma_data"
CHROMA_DIR = (_chroma_path if _chroma_path.is_absolute() else BASE_DIR / _chroma_path).resolve()
DOCUMENT_DIR = BASE_DIR / "private_documents"


ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
}
