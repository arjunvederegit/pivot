from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
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
    #: Per-dataset freshness windows for `app.canonical.provenance`. Rosters and standings
    #: refresh every 6 h (worker), so they share `nba_api_stale_after_seconds`; player and
    #: team statistics refresh daily, so "stale" is two missed refreshes, not one late job.
    #: A contract snapshot is a user-supplied file on its own cadence.
    stats_stale_after_seconds: int = 172_800
    contracts_stale_after_seconds: int = 2_592_000
    #: Teams and players are identity records (the package's static tables plus roster-created
    #: players); they do not age by the day, so they get their own, long window.
    identity_stale_after_seconds: int = 2_592_000
    #: Injury status changes by the hour around games; a day-old report is already wrong.
    injuries_stale_after_seconds: int = 21_600
    #: A finalized (completed-season) dataset is still re-verified now and then — upstream
    #: corrections happen — so one not re-fetched for this long carries an advisory note.
    #: It never turns a completed season "stale" by itself.
    finalized_reverify_after_days: int = 365
    nba_api_user_agent: str = ""
    nba_api_http_proxy: str = ""

    #: An explicit override of the analysis season (`CURRENT_SEASON=2025-26`). Blank — the default —
    #: means *derive it from the calendar* (`app.canonical.calendar`): the season the league is in
    #: once its regular season has been under way `season_rollover_grace_days`, otherwise the last
    #: completed one. A pinned value still wins, and `data-status` reports when it has fallen
    #: behind the calendar; leaving it blank is what stops the season needing a hand edit each October.
    pinned_season: str = Field(default="", validation_alias="CURRENT_SEASON")
    season_rollover_grace_days: int = 14
    #: R8.2. An explicit override of the ROSTER season (`ROSTER_SEASON=2026-27`). Blank — the
    #: default — follows the league year (`SeasonContext.roster_season`), independently of
    #: `CURRENT_SEASON`: a roster does not wait for statistics. See `roster_season_preferred`.
    pinned_roster_season: str = Field(default="", validation_alias="ROSTER_SEASON")
    #: The MODELLING window. Every served estimate — player impact, skills, the rotation
    #: allocation, the R3 conversion coefficient — is fitted and served on exactly these
    #: seasons, and R7 did not widen it. Blank (the default) = the three seasons ending at the
    #: analysis season, which is `2023-24,2024-25,2025-26` today; an explicit value wins.
    history_seasons: str = ""
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
    #: Blank = the ten seasons ending at the analysis season (`2016-17` … `2025-26` today).
    corpus_seasons: str = ""
    # League year whose cap parameters govern trade legality (trades executed in July
    # 2026 fall under the 2026-27 cap even though the latest completed stats season
    # is 2025-26).
    cap_league_year: str = "2026-27"

    # --- BALLDONTLIE (optional; server-side only; Pivot runs without it) ---
    #: Credential for api.balldontlie.io. Read from the environment only, wrapped so it
    #: never appears in a repr, a log line or an error message.
    balldontlie_api_key: SecretStr = SecretStr("")
    #: free | trial | all_star | goat | all_access. Sets the client's request pacing; the
    #: tier does not unlock anything by itself. Defaults to `trial` (5 requests/minute), the
    #: most conservative setting, because a wrong guess upward burns a 48-hour trial on 429s.
    balldontlie_tier: str = "trial"
    balldontlie_base_url: str = "https://api.balldontlie.io"
    #: The OpenAPI document serves NBA under `/nba/v1`; the narrative docs show `/v1`. The
    #: harness probes which one the live API accepts, so this is configuration, not belief.
    balldontlie_api_prefix: str = "/nba/v1"
    balldontlie_timeout_seconds: float = 30.0
    balldontlie_max_retries: int = 3
    #: Optional path of a JSONL request ledger (endpoint, parameter names, status, latency).
    balldontlie_request_log: str = ""

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
    def balldontlie_configured(self) -> bool:
        return bool(self.balldontlie_api_key.get_secret_value().strip())

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
    def season_context(self):  # -> SeasonContext
        """Today's place in the NBA calendar (``app.canonical.calendar``)."""
        from app.canonical.calendar import season_context, today_in_league

        return season_context(today_in_league(), grace_days=self.season_rollover_grace_days)

    @property
    def current_season(self) -> str:
        """The season every tool reads: the pinned override, else the calendar's analysis season.

        A property, not a field, so a long-running server or worker crosses the rollover date
        without a restart. Always a validated ``"YYYY-YY"`` label.
        """
        from app.canonical.seasons import Season

        if self.pinned_season.strip():
            return Season.parse(self.pinned_season.strip()).label
        return self.season_context.analysis_season.label

    @property
    def roster_season_preferred(self) -> str:
        """R8.2. The season rosters *should* be read from, before looking at what is stored.

        ``ROSTER_SEASON`` if set, otherwise the league year (2026-27 from 1 July 2026). It does
        **not** depend on ``CURRENT_SEASON``: a pin on the statistics season (the old default was
        ``CURRENT_SEASON=2025-26``) must not drag rosters back to last season — that coupling is
        exactly what R8.2 removes. A deliberately historical session sets both variables.

        Use ``app.canonical.rosters.resolve_roster_season(db)`` to also account for a roster
        snapshot that has not been ingested yet.
        """
        from app.canonical.seasons import Season

        if self.pinned_roster_season.strip():
            return Season.parse(self.pinned_roster_season.strip()).label
        return self.season_context.roster_season.label

    @property
    def history_season_list(self) -> list[str]:
        explicit = [s.strip() for s in self.history_seasons.split(",") if s.strip()]
        if explicit:
            return explicit
        from app.canonical.seasons import Season

        end = Season.parse(self.current_season)
        return [Season(end.start_year - back).label for back in (2, 1, 0)]

    @property
    def sync_season_list(self) -> list[str]:
        """The seasons a routine statistics refresh covers.

        The modelling window, plus the league's own season as soon as its regular season has
        started — so the new season's figures are already stored when the tools roll over to it,
        rather than appearing a day after.
        """
        seasons = self.history_season_list
        context = self.season_context
        league = context.league_season.label
        if context.regular_season_stats_expected and league not in seasons:
            seasons = [*seasons, league]
        return seasons

    @property
    def corpus_season_list(self) -> list[str]:
        """Every season the corpus may be described by, modelling window included."""
        explicit = {s.strip() for s in self.corpus_seasons.split(",") if s.strip()}
        if not explicit:
            from app.canonical.seasons import Season

            end = Season.parse(self.current_season)
            explicit = {Season(end.start_year - back).label for back in range(9, -1, -1)}
        return sorted(explicit | set(self.history_season_list))


@lru_cache
def get_settings() -> Settings:
    return Settings()
