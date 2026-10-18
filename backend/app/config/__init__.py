from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
CAP_RULES_DIR = Path(__file__).resolve().parent / "cap_rules"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: The filename keeps its pre-Pivot spelling deliberately: it points at a real local
    #: database that developers already have on disk. Renaming the default would silently
    #: open an empty database instead of theirs.
    database_url: str = "sqlite:///./tradelab.db"
    redis_url: str = ""

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000"
    admin_token: str = ""

    nba_api_timeout_seconds: float = 45.0
    nba_api_max_retries: int = 4
    nba_api_min_request_interval_seconds: float = 0.75
    nba_api_max_concurrency: int = 2
    nba_api_cache_ttl_seconds: int = 3600
    nba_api_stale_after_seconds: int = 86400
    nba_api_user_agent: str = ""
    nba_api_http_proxy: str = ""

    current_season: str = "2025-26"
    #: The MODELLING window. Every served estimate — player impact, skills, the rotation
    #: allocation, the R3 conversion coefficient — is fitted and served on exactly these
    #: seasons, and R7 did not widen it.
    history_seasons: str = "2023-24,2024-25,2025-26"
    #: The seasons the historical-trade CORPUS may be described by. Deliberately a
    #: separate setting: `player_season_stats` is read by two consumers with different
    #: questions. `recency_weighted_features` collapses `history_seasons` into the window
    #: it serves; `services/comparables` scores each season on its own within-season
    #: z-scores and never collapses across them, so a 2016-17 trade can be priced without
    #: any 2016-17 number reaching a served estimate.
    #:
    #: Widening this was measured before it shipped (R7-2): the served window frame is
    #: byte-identical at 632 rows x 33 columns, and every R3 calibration figure reproduces
    #: to full float precision — 14.976967215546017, SE 1.5279397396294392,
    #: R2 0.6235734193376163 — on a season frame that grows from 1,714 rows to 5,483.
    corpus_seasons: str = (
        "2016-17,2017-18,2018-19,2019-20,2020-21,2021-22,2022-23,2023-24,2024-25,2025-26"
    )
    # League year whose cap parameters govern trade legality (trades executed in July
    # 2026 fall under the 2026-27 cap even though the latest completed stats season
    # is 2025-26).
    cap_league_year: str = "2026-27"

    contract_data_provider: str = ""
    contract_data_api_key: str = ""
    contract_data_file: str = ""

    injury_data_provider: str = ""
    live_data_enabled: bool = False

    anthropic_api_key: str = ""

    # --- Local media asset roots (user-supplied datasets; gitignored) ---
    asset_logos_dir: str = ""  # default: <repo>/nbalogos
    asset_player_images_dir: str = ""  # default: <repo>/nbaplayerimages
    # --- Kaggle historical dataset cache (kagglehub) ---
    kaggle_data_dir: str = ""  # default: kagglehub's own cache; override for CI/servers

    # --- Ask Pivot / Pivot Intelligence (experimental; FICTIONAL corpus only) ---
    #: Master switch for the synthetic research assistant. Off by default everywhere: with
    #: it off, `POST /assistant/ask` is a 404 and `GET /assistant/status` reports only
    #: `enabled: false`. It never reads or writes the NBA database either way.
    assistant_enabled: bool = False
    #: The inference route is unauthenticated and expensive, so the assistant's routes
    #: answer loopback requests only (peer, forwarded-for, Host and forwarded-host). Setting
    #: this lets a non-loopback client in (a docker bridge, a LAN demo); it is not a
    #: substitute for real auth, and nothing public should ever set it.
    assistant_allow_remote: bool = False
    #: Accept the dev-only `options` block on the ask route (prompt/retrieval variants, k).
    assistant_dev_options: bool = False
    ollama_base_url: str = "http://localhost:11434"
    assistant_chat_model: str = "qwen3.5:2b"
    assistant_embedding_model: str = "nomic-embed-text"
    #: Wall-clock budget for one answer, end to end. A 2-3B local model on a laptop takes
    #: tens of seconds on a cold start; past this the request is cancelled and is a 504.
    assistant_request_timeout_seconds: float = 150.0
    #: Concurrent answers allowed at once. Local Ollama serialises generation anyway; a
    #: second request beyond this is a 503 `assistant_busy`, not a silent queue.
    assistant_max_in_flight: int = 1
    assistant_num_ctx: int = 8192
    assistant_max_output_tokens: int = 900
    # --- LangSmith tracing for Ask Pivot (opt-in; traces LEAVE this machine) ---
    #: The switch and the project are read from Pivot-specific variables, NOT the SDK's own
    #: `LANGSMITH_TRACING` / `LANGSMITH_PROJECT`: a developer who exports those for another
    #: project (the usual LangSmith setup) must not have Ask Pivot questions uploaded, or
    #: filed under that other project. A `validation_alias` replaces the field name as the
    #: environment / `.env` key, so `LANGSMITH_TRACING` is simply not read. Tracing runs only
    #: with `ASSISTANT_TRACING=true` *and* a key (`tracing.tracing_active`).
    langsmith_tracing: bool = Field(default=False, validation_alias="ASSISTANT_TRACING")
    #: The key and endpoint keep the SDK's names: neither can switch tracing on by itself.
    langsmith_api_key: str = ""
    langsmith_project: str = Field(default="pivot-intelligence-v2", validation_alias="ASSISTANT_LANGSMITH_PROJECT")
    langsmith_endpoint: str = ""  # default: the SDK's own (api.smith.langchain.com)

    @property
    def logos_dir(self) -> Path:
        return (
            Path(self.asset_logos_dir) if self.asset_logos_dir else BACKEND_DIR.parent / "nbalogos"
        )

    @property
    def player_images_dir(self) -> Path:
        return (
            Path(self.asset_player_images_dir)
            if self.asset_player_images_dir
            else BACKEND_DIR.parent / "nbaplayerimages"
        )

    @property
    def contract_data_path(self) -> Path | None:
        """`CONTRACT_DATA_FILE` resolved against the repo root, not the process CWD.

        A relative path such as `data/imports/contracts/players.html` resolved
        differently depending on where the process started — it worked from the repo
        root and silently found nothing from `backend/`, which is where `make` runs
        every backend command from."""
        if not self.contract_data_file:
            return None
        candidate = Path(self.contract_data_file)
        return candidate if candidate.is_absolute() else (BACKEND_DIR.parent / candidate)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def history_season_list(self) -> list[str]:
        return [s.strip() for s in self.history_seasons.split(",") if s.strip()]

    @property
    def corpus_season_list(self) -> list[str]:
        """Every season the corpus may be described by, modelling window included."""
        seasons = {s.strip() for s in self.corpus_seasons.split(",") if s.strip()}
        return sorted(seasons | set(self.history_season_list))


@lru_cache
def get_settings() -> Settings:
    return Settings()
