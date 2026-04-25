import os
from dataclasses import dataclass


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable '{name}' is required.")
    return value


@dataclass(frozen=True)
class Settings:
    rakuraku_register_url: str
    rakuraku_update_url: str
    rakuraku_token: str
    bakuraku_file_upload_url: str
    bakuraku_application_url: str
    bakuraku_status_url: str
    bakuraku_token: str
    polling_interval_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            rakuraku_register_url=_required_env("RAKURAKU_REGISTER_URL"),
            rakuraku_update_url=_required_env("RAKURAKU_UPDATE_URL"),
            rakuraku_token=_required_env("RAKURAKU_TOKEN"),
            bakuraku_file_upload_url=_required_env("BAKURAKU_FILE_UPLOAD_URL"),
            bakuraku_application_url=_required_env("BAKURAKU_APPLICATION_URL"),
            bakuraku_status_url=_required_env("BAKURAKU_STATUS_URL"),
            bakuraku_token=_required_env("BAKURAKU_TOKEN"),
            polling_interval_seconds=int(_required_env("POLLING_INTERVAL_SECONDS")),
        )


settings = Settings.from_env()
