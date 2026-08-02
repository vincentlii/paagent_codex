"""Pydantic settings models for PA Agent."""

from __future__ import annotations

import json
import logging
import os
from contextlib import suppress
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DecisionStance = Literal["conservative", "balanced", "aggressive", "extreme_aggressive"]
DataSourceKind = Literal[
    "mt5", "tradingview", "akshare", "eastmoney", "tushare", "binance_usdm"
]
VpaMode = Literal["off", "shadow", "soft"]
MIN_ANALYSIS_BARS = 20


def validate_api_key_value(value: str, *, source: str = "API Key") -> str:
    """Return a trimmed header-safe secret or raise without echoing it."""
    key = str(value or "").strip()
    if key and (not key.isascii() or any(char.isspace() for char in key)):
        raise ValueError(f"{source} must be one single-line ASCII token")
    return key


class AIProviderSettings(BaseModel):
    """AI provider connection and behaviour settings."""

    model_config = ConfigDict(extra="ignore")

    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    api_key_encrypted: str = ""
    thinking: bool = True
    reasoning_effort: Literal["low", "medium", "high", "max"] = "high"
    context_window: int = Field(default=2_000_000, ge=1)
    structured_output_mode: Literal[
        "auto",
        "json_schema",
        "json_object",
        "text",
    ] = "auto"

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, value: str) -> str:
        return validate_api_key_value(value)


class PromptSettings(BaseModel):
    """Prompt assembly tuning (accuracy-oriented defaults)."""

    model_config = ConfigDict(extra="ignore")

    #: When True, Stage 2 loads every strategy .txt (legacy/test behaviour).
    stage2_load_full_strategy_library: bool = False
    experience_max_entries: int = Field(default=0, ge=0, le=10)
    experience_max_chars_per_entry: int = Field(default=400, ge=100, le=4000)
    #: Inject pattern判定表 + 速查 brief into Stage 1 user prompt (reduces missed tags).
    stage1_inject_pattern_briefs: bool = True
    #: ``shadow`` keeps PA-only official; ``soft`` is reserved for research A/A/B/B runs.
    vpa_mode: VpaMode | None = None
    #: Legacy switch retained for existing settings files. New writes set ``vpa_mode``.
    vpa_enabled: bool = False


class ValidationSettings(BaseModel):
    """Post-LLM validation behaviour."""

    model_config = ConfigDict(extra="ignore")

    #: Re-call API with structured feedback when validation fails (format errors).
    retry_enabled: bool = True
    retry_max: int = Field(default=3, ge=0, le=5)
    #: Max retries for category=c semantic errors (subset only).
    retry_max_semantic: int = Field(default=1, ge=0, le=3)
    retry_stage2: bool = True


class GeneralSettings(BaseModel):
    """UI and data-feed general settings."""

    model_config = ConfigDict(extra="ignore")

    analysis_bar_count: int = Field(default=100, ge=MIN_ANALYSIS_BARS, le=5000)
    #: One optional slower chart for structural background; current timeframe remains authoritative.
    higher_timeframe_enabled: bool = True
    #: Empty = universal default mapping; a supported timeframe explicitly overrides it.
    higher_timeframe_override: str = ""
    higher_timeframe_bar_count: int = Field(default=60, ge=20, le=300)
    refresh_interval_ms: int = 1000
    context_warning_threshold_pct: float = 80.0
    last_data_source: DataSourceKind = "tradingview"
    #: A-share K-line adjust for East Money / Baostock (qfq=前复权)
    kline_adjust: Literal["qfq", "hfq", "none"] = "qfq"
    #: TradingView 交易所；空字符串 =（自动）依次探测预设列表
    last_tradingview_exchange: str = ""
    last_symbol: str = "XAUUSD"
    last_timeframe: str = "15m"
    decision_flow_auto_play: bool = True
    decision_flow_play_seconds: int = 50
    #: 阶段二给出限价/突破/市价单时：警报音、弹窗，并自动切到「决策」页（跳过决策树可视化演示）
    alert_on_order_opportunity: bool = True
    incremental_max_new_bars: int = Field(default=10, ge=0, le=500)
    #: 阶段二交易倾向：balanced=默认；conservative/aggressive 逐级调整下单意愿
    decision_stance: DecisionStance = "balanced"
    #: 决策树可视化：在「整图适配」基础上的缩放百分比（100=与适配一致；可任意放大，仅下限 10%）
    decision_flow_default_zoom_pct: int = Field(default=600, ge=10)
    #: 「实时」页思考过程/撰写回答框与追问输入框的等宽字体字号（pt）
    stream_pane_font_pt: int = Field(default=11, ge=8, le=28)
    #: K 线图上 #序号 标签的字号（pt）
    chart_seq_label_font_pt: int = Field(default=11, ge=6, le=24)
    #: 两阶段分析结束后是否自动恢复 K 线图表实时刷新
    auto_resume_chart_after_analysis: bool = False
    #: 持续跟踪分析：有新K线收盘时自动触发新一轮分析
    keep_analysis: bool = False
    #: 重试后取消持续跟踪分析：校验失败触发重试后自动关闭 keep_analysis
    cancel_keep_analysis_on_retry: bool = False
    #: 程序执行门槛：低于阈值的模型订单在持久化前转为不下单并留下审计 trace。
    decision_confidence_threshold: int = Field(default=40, ge=0, le=100)
    #: Closed-OHLC counterfactual review horizon. It never represents a live fill.
    counterfactual_horizon_bars: int = Field(default=20, ge=1, le=500)
    #: 开启下根K线预期功能；关闭时不向模型请求该预测，节省 token
    enable_next_bar_prediction: bool = False

    @field_validator("last_data_source", mode="before")
    @classmethod
    def _coerce_legacy_data_source(cls, v: object) -> object:
        if v == "yfinance":
            return "mt5"
        if v in ("adata", "a_share"):
            return "akshare"
        if v == "eastmoney":
            return "eastmoney"
        if v == "tushare":
            return "tushare"
        return v

    @field_validator("decision_flow_default_zoom_pct", mode="before")
    @classmethod
    def _coerce_zoom_pct(cls, v: object) -> object:
        if v is None:
            return 50
        return v


class MonitoringAssetSettings(BaseModel):
    """One independently sourced asset monitored by the headless runtime."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    source: DataSourceKind = "tradingview"
    exchange: str = ""
    symbol: str
    timeframe: str

    @field_validator("source", mode="before")
    @classmethod
    def _canonical_source(cls, value: object) -> str:
        return str(value or "").strip().lower()

    @field_validator("exchange", mode="before")
    @classmethod
    def _canonical_exchange(cls, value: object) -> str:
        return str(value or "").strip().upper()

    @field_validator("symbol", mode="before")
    @classmethod
    def _canonical_symbol(cls, value: object) -> str:
        text = str(value or "").strip().upper()
        if not text:
            raise ValueError("monitoring asset symbol is required")
        return text

    @field_validator("timeframe", mode="before")
    @classmethod
    def _canonical_timeframe(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("monitoring asset timeframe is required")
        if text.endswith("M"):
            return f"{text[:-1].lower()}M"
        return text.lower()


class MonitoringSettings(BaseModel):
    """Bounded multi-asset analysis scheduling settings."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    max_concurrency: int = Field(default=2, ge=1, le=20)
    max_pending_jobs: int = Field(default=100, ge=1, le=10_000)
    max_pending_per_asset: int = Field(default=2, ge=1, le=100)
    dedupe_history: int = Field(default=10_000, ge=1, le=1_000_000)
    max_data_concurrency: int = Field(default=4, ge=1, le=20)
    poll_interval_seconds: float = Field(default=15.0, ge=1.0, le=3600.0)
    run_on_startup: bool = True
    max_analysis_retries: int = Field(default=2, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=30.0, ge=1.0, le=3600.0)
    analysis_timeout_seconds: float = Field(default=900.0, ge=30.0, le=7200.0)
    shutdown_timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    assets: list[MonitoringAssetSettings] = Field(default_factory=list)

    @model_validator(mode="after")
    def _queue_covers_workers(self) -> MonitoringSettings:
        if self.max_pending_jobs < self.max_concurrency:
            raise ValueError("max_pending_jobs must be at least max_concurrency")
        enabled = [asset for asset in self.assets if asset.enabled]
        seen: set[tuple[str, str, str, str]] = set()
        by_instrument: dict[tuple[str, str, str], list[str]] = {}
        for asset in enabled:
            key = (asset.source, asset.exchange, asset.symbol, asset.timeframe)
            if key in seen:
                raise ValueError(
                    "duplicate monitoring asset: "
                    f"{asset.source}:{asset.exchange or 'auto'}:"
                    f"{asset.symbol}:{asset.timeframe}"
                )
            seen.add(key)
            instrument = (asset.source, asset.symbol, asset.timeframe)
            exchanges = by_instrument.setdefault(instrument, [])
            if exchanges and (not asset.exchange or any(not item for item in exchanges)):
                raise ValueError(
                    "ambiguous monitoring asset: an automatic exchange cannot coexist "
                    "with another subscription for the same source/symbol/timeframe"
                )
            exchanges.append(asset.exchange)
        return self


class PaperTradingSettings(BaseModel):
    """Local simulation settings; this never authorizes private venue orders."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    initial_equity: float = Field(default=10_000.0, gt=0)
    risk_fraction: float = Field(default=0.01, gt=0, le=1)
    max_portfolio_risk_fraction: float = Field(default=0.03, gt=0, le=1)
    max_leverage: float = Field(default=3.0, gt=0, le=100)
    fee_bps: float = Field(default=5.0, ge=0, le=1000)
    slippage_bps: float = Field(default=2.0, ge=0, le=1000)


_FEISHU_CONFIG_KEYS = (
    "enabled",
    "webhook_url",
    "secret",
    "app_id",
    "app_secret",
    "notify_on_order_only",
)


class FeishuSettings(BaseModel):
    """Feishu bot notification settings (persisted in settings.json)."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    webhook_url: str = ""
    secret: str = ""
    app_id: str = ""
    app_secret: str = ""
    #: True = only push when there is an order opportunity.
    notify_on_order_only: bool = True


class TushareSettings(BaseModel):
    """Tushare Pro data source settings (persisted in ignored settings.json)."""

    model_config = ConfigDict(extra="ignore")

    token: str = ""


class PushPlusSettings(BaseModel):
    """PushPlus notification settings (settings.json only; no GUI)."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    token: str = ""


class Settings(BaseModel):
    """Root settings object persisted to config/settings.json."""

    model_config = ConfigDict(extra="ignore")

    provider: AIProviderSettings = Field(default_factory=AIProviderSettings)
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    prompt: PromptSettings = Field(default_factory=PromptSettings)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)
    paper_trading: PaperTradingSettings = Field(default_factory=PaperTradingSettings)
    feishu: FeishuSettings = Field(default_factory=FeishuSettings)
    pushplus: PushPlusSettings = Field(default_factory=PushPlusSettings)
    tushare: TushareSettings = Field(default_factory=TushareSettings)


def provider_api_key_configured(settings: Settings | None) -> bool:
    """Return True when a non-empty API key is loaded in memory."""
    if settings is None:
        return False
    return bool((settings.provider.api_key or "").strip())


# ── Persistence ───────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)


def _migrate_legacy_feishu_json(raw: dict, settings_path: Path) -> bool:
    """Merge legacy config/feishu.json into settings.feishu when needed."""
    legacy_path = settings_path.parent / "feishu.json"
    if not legacy_path.exists():
        return False

    feishu = raw.setdefault("feishu", {})
    if (feishu.get("webhook_url") or "").strip():
        return False

    try:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("legacy feishu.json unreadable (%s); skipping migration", exc)
        return False

    migrated = False
    for key in _FEISHU_CONFIG_KEYS:
        if key not in legacy:
            continue
        value = legacy.get(key)
        if value in (None, ""):
            continue
        if feishu.get(key) in (None, ""):
            feishu[key] = value
            migrated = True
    if migrated:
        logger.info("Migrated Feishu config from %s into settings.json", legacy_path)
    return migrated


def load_settings(path: Path | None = None) -> Settings:
    """Load settings from *path* (default: SETTINGS_JSON_PATH).

    Returns default Settings and writes them to disk if the file is absent.
    """
    from pa_agent.config.paths import SETTINGS_JSON_PATH

    path = path or SETTINGS_JSON_PATH

    if not path.exists():
        defaults = Settings()
        save_settings(defaults, path)
        return defaults

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("settings.json unreadable (%s); using defaults", exc)
        return Settings()

    # Migrate legacy field names
    general = raw.get("general", {})
    if "cost_warning_threshold_pct" in general and "context_warning_threshold_pct" not in general:
        general["context_warning_threshold_pct"] = general.pop("cost_warning_threshold_pct")
    general.pop("last_htf_text", None)
    from pa_agent.data.market_defaults import migrate_general_gold_defaults

    migrate_general_gold_defaults(general)
    if "default_bar_count" in general and "analysis_bar_count" not in general:
        general["analysis_bar_count"] = general.pop("default_bar_count")
    raw["general"] = general
    provider = raw.get("provider", {})
    provider.pop("pricing", None)
    legacy_plaintext_key = bool(str(provider.get("api_key") or "").strip())
    encrypted_key = str(provider.get("api_key_encrypted") or "").strip()
    if not legacy_plaintext_key and encrypted_key:
        from pa_agent.security.api_key_store import decrypt_api_key

        provider["api_key"] = decrypt_api_key(encrypted_key, path)
    raw["provider"] = provider

    # Keep the in-memory plaintext field available to provider clients.
    raw.setdefault("provider", {}).setdefault("api_key", "")

    migrated_feishu = _migrate_legacy_feishu_json(raw, path)
    settings = Settings.model_validate(raw)
    dirty = migrated_feishu or legacy_plaintext_key
    if (
        settings.pushplus.enabled
        and not settings.pushplus.token.strip()
        and not (os.environ.get("PUSHPLUS_TOKEN") or "").strip()
    ):
        settings.pushplus.enabled = False
        logger.info(
            "PushPlus enabled but token empty — auto-disabled "
            "(Feishu notifications unaffected)"
        )
        dirty = True
    if dirty:
        save_settings(settings, path)
    return settings


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Persist settings to *path* (default: SETTINGS_JSON_PATH)."""
    from pa_agent.config.paths import SETTINGS_JSON_PATH

    path = path or SETTINGS_JSON_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    data = settings.model_dump()
    provider = data.setdefault("provider", {})
    plaintext_key = validate_api_key_value(str(provider.get("api_key") or ""))
    if plaintext_key:
        from pa_agent.security.api_key_store import encrypt_api_key

        provider["api_key_encrypted"] = encrypt_api_key(plaintext_key, path)
    provider["api_key"] = ""

    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2)
        with temp_path.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        with suppress(OSError):
            path.chmod(0o600)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
