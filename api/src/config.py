from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = API_DIR.parent


class Config(BaseSettings):
    """Application configuration. All secrets come from environment variables / .env (never from code)."""

    model_config = SettingsConfigDict(
        env_file=[REPO_DIR / ".env", API_DIR / ".env"],
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-5.4-mini"

    # Odoo (free trial, CRM app)
    ODOO_URL: str = ""
    ODOO_DB: str = ""
    ODOO_USERNAME: str = ""
    ODOO_API_KEY: str = ""

    # Pricing
    PRICE_SHEET_PATH: Path = API_DIR / "data" / "price_sheet.csv"
    CURRENCY: str = "SAR"


config = Config()
