from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class MinioSettings:
    endpoint: str = ""
    access_key: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    bucket: str = "ruiware-templates"
    prefix: str = ""
    read_prefix: str = "publishers"
    secure: bool = False

    @property
    def configured(self) -> bool:
        return all((self.endpoint, self.access_key, self.secret_key, self.bucket, self.prefix, self.read_prefix))

    @property
    def read_configured(self) -> bool:
        return all((self.endpoint, self.access_key, self.secret_key, self.bucket, self.read_prefix))


@dataclass(frozen=True)
class Settings:
    api_url: str = "http://127.0.0.1:8010"
    api_key: str = field(default="", repr=False)
    draft_db: Path = PROJECT_ROOT / ".runtime" / "components.sqlite3"
    mcp_token: str = field(default="", repr=False)
    public_base_url: str = "http://127.0.0.1:8020"
    minio: MinioSettings = field(default_factory=MinioSettings)


def load_settings(root: Path = PROJECT_ROOT) -> Settings:
    # Do not mutate the process environment; an explicitly empty env value wins too.
    values = {**dotenv_values(root / ".env"), **dotenv_values(root / ".env.local"), **os.environ}
    return Settings(
        api_url=values.get("RUIWARE_TEMPLATE_API_URL") or "http://127.0.0.1:8010",
        api_key=values.get("RUIWARE_PUBLIC_API_KEY") or "",
        draft_db=Path(values.get("COMPONENT_DRAFT_DB") or PROJECT_ROOT / ".runtime" / "components.sqlite3"),
        mcp_token=values.get("COMPONENT_MCP_TOKEN") or "",
        public_base_url=values.get("COMPONENT_PUBLIC_BASE_URL") or "http://127.0.0.1:8020",
        minio=MinioSettings(
            endpoint=(values.get("RUIWARE_MINIO_ENDPOINT") or "").removeprefix("http://").removeprefix("https://").rstrip("/"),
            access_key=values.get("RUIWARE_MINIO_ACCESS_KEY") or "",
            secret_key=values.get("RUIWARE_MINIO_SECRET_KEY") or "",
            bucket=values.get("RUIWARE_MINIO_BUCKET") or "ruiware-templates",
            prefix=(values.get("RUIWARE_MINIO_PREFIX") or "").strip("/"),
            read_prefix=(values.get("RUIWARE_MINIO_READ_PREFIX") or "publishers").strip("/"),
            secure=(values.get("RUIWARE_MINIO_SECURE") or "false").lower() in {"1", "true", "yes"},
        ),
    )
