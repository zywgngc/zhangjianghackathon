import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "data")).resolve())
    base_url: str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "http://10.176.55.210:8080/v1"))
    api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("GAME_MODEL", "qwen3.8-max"))
    concurrency: int = field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT_RUNS", "2")))
    max_repairs: int = field(default_factory=lambda: int(os.getenv("MAX_REPAIRS", "2")))
    max_calls: int = field(default_factory=lambda: int(os.getenv("MAX_MODEL_CALLS", "16")))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("MAX_TOTAL_TOKENS", "100000")))
    run_timeout: int = field(default_factory=lambda: int(os.getenv("RUN_TIMEOUT_SECONDS", "1200")))
    model_timeout: int = field(default_factory=lambda: int(os.getenv("MODEL_TIMEOUT_SECONDS", "300")))
    browser_sandbox: bool = field(default_factory=lambda: os.getenv("BROWSER_SANDBOX", "true").lower() == "true")
    browser_timeout: float = field(default_factory=lambda: float(os.getenv("BROWSER_TIMEOUT_SECONDS", "60")))
    vision_qa: bool = field(default_factory=lambda: os.getenv("VISION_QA", "true").lower() == "true")
    stream_build: bool = field(default_factory=lambda: os.getenv("STREAM_BUILD", "true").lower() == "true")
    reasoning_effort: str = field(default_factory=lambda: os.getenv("MODEL_REASONING_EFFORT", "none"))
    api_token: str = field(default_factory=lambda: os.getenv("HARNESS_API_TOKEN", ""))
    rag_dir: str = field(default_factory=lambda: os.getenv("RAG_DIR", ""))
    rag_url: str = field(default_factory=lambda: os.getenv("RAG_API_URL", ""))
    rag_token: str = field(default_factory=lambda: os.getenv("RAG_API_TOKEN", ""))
    portal_url: str = field(default_factory=lambda: os.getenv("MATERIAL_PORTAL_URL", ""))
    portal_token: str = field(default_factory=lambda: os.getenv("MATERIAL_PORTAL_TOKEN", ""))
    portal_schema: Path = field(default_factory=lambda: Path(os.getenv("MATERIAL_PORTAL_SCHEMA", str(Path(__file__).resolve().parents[1] / "webmcp/material-portal.schema.json"))))

    def role_model(self, role: str) -> str:
        return os.getenv("MODEL_" + role.upper(), self.model)


def load_settings() -> Settings:
    load_dotenv()
    settings = Settings()
    if not 1 <= settings.concurrency <= 8 or not 0 <= settings.max_repairs <= 5:
        raise ValueError("concurrency must be 1..8 and max_repairs must be 0..5")
    return settings
