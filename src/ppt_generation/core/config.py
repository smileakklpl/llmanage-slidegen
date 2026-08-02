"""
集中管理路徑常數與 LLM 憑證載入
================================
對應 docs/圖表原生性與資料同步設計.md §6.1。

憑證載入優先序：
1. 環境變數（CI／容器部署走這條）
2. 回退讀取 `.venv/api_key/` 下的檔案（本機開發與競賽 Demo）

安全約定：金鑰值永不寫入日誌、錯誤訊息或產出檔案。
所有錯誤訊息只提示「找不到哪個 key 名稱」，不回顯值本身。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# 路徑常數
# ---------------------------------------------------------------------------
# config.py 位於 <repo>/src/ppt_generation/core/config.py，往上四層即專案根目錄。
PROJECT_ROOT = Path(__file__).resolve().parents[3]

SOURCE_DIR = PROJECT_ROOT / "source"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
DOCS_DIR = PROJECT_ROOT / "docs"

TEMPLATE_PPTX = SOURCE_DIR / "template.pptx"

#: API 金鑰目錄。`.venv` 已被 .gitignore 排除，不會進版控。
API_KEY_DIR = PROJECT_ROOT / ".venv" / "api_key"
DEFAULT_API_KEY_FILE = API_KEY_DIR / "key.txt"


class CredentialNotFoundError(RuntimeError):
    """找不到可用的 LLM 憑證。錯誤訊息只含 key 名稱，不含任何值。"""


# ---------------------------------------------------------------------------
# 憑證載入
# ---------------------------------------------------------------------------
def _parse_key_file(path: Path) -> dict[str, str]:
    """
    解析金鑰檔，支援兩種格式：

    1. 整檔即金鑰（單行或多行，去除空白後視為一個值），
       以 ``"__default__"`` 作為 key 存放。
    2. ``KEY=VALUE`` 逐行格式，可放多組後端憑證。
       ``#`` 開頭的行視為註解。

    兩種格式可混用：含 ``=`` 的行走格式 2，其餘走格式 1。
    """
    if not path.exists():
        return {}

    parsed: dict[str, str] = {}
    bare_lines: list[str] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key:
                parsed[key] = value
        else:
            bare_lines.append(line)

    if bare_lines and "__default__" not in parsed:
        # 整檔即金鑰的情況，取第一個非空行，避免把多行意外拼成一串。
        parsed["__default__"] = bare_lines[0]

    return parsed


@lru_cache(maxsize=8)
def _key_file_entries(path_str: str) -> tuple[tuple[str, str], ...]:
    """快取金鑰檔解析結果，避免每次 LLM 呼叫都重讀檔案。"""
    return tuple(_parse_key_file(Path(path_str)).items())


def load_credential(
    *env_names: str,
    key_file: Path | None = None,
    required: bool = True,
) -> str | None:
    """
    依序嘗試取得憑證。

    Args:
        env_names: 依序嘗試的環境變數名稱，例如
            ``load_credential("LLM_API_KEY", "OPENAI_API_KEY")``。
            同時也會用這些名稱去金鑰檔的 ``KEY=VALUE`` 內容中查找。
        key_file: 金鑰檔路徑，預設 ``.venv/api_key/key.txt``。
        required: 找不到時是否拋錯。False 則回傳 None。

    Returns:
        憑證字串，或 None（``required=False`` 且找不到時）。

    Raises:
        CredentialNotFoundError: ``required=True`` 且所有來源都找不到。
    """
    for name in env_names:
        value = os.getenv(name)

        if value and value.strip():
            return value.strip()

    target_file = key_file or DEFAULT_API_KEY_FILE
    entries = dict(_key_file_entries(str(target_file)))

    for name in env_names:
        value = entries.get(name)

        if value:
            return value

    default_value = entries.get("__default__")

    if default_value:
        return default_value

    if not required:
        return None

    raise CredentialNotFoundError(
        "找不到 LLM 憑證。請設定環境變數 "
        f"{' 或 '.join(env_names) or '(未指定)'}，"
        f"或將金鑰寫入 {target_file}（相對於專案根目錄）。"
    )


def reset_credential_cache() -> None:
    """清除金鑰檔快取。金鑰檔在執行期間被修改後呼叫。"""
    _key_file_entries.cache_clear()


# ---------------------------------------------------------------------------
# LLM 設定
# ---------------------------------------------------------------------------
#: 已知的 OpenAI 相容端點捷徑。設定 ``LLM_PROVIDER`` 即可，不必手抄 URL。
PROVIDER_BASE_URLS = {
    "ollama": "http://localhost:11434/v1",
    "vllm": "http://localhost:8000/v1",
    # Google Gemini / Gemma 的 OpenAI 相容層
    # https://ai.google.dev/gemini-api/docs/openai
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/",
}

#: 走 OpenAI SDK 的 provider（含各家 OpenAI 相容端點）。
OPENAI_COMPATIBLE_PROVIDERS = frozenset(
    {"openai", "google", "vllm", "ollama", "litellm"}
)

#: 明確需要 API key 的公開 provider；地端端點不因沒有 key 而失敗。
API_KEY_REQUIRED_PROVIDERS = frozenset({"openai", "google"})

#: ``local_only`` 僅允許可由部署者控制的本地推論後端。
LLM_PRIVACY_MODES = frozenset({"standard", "local_only"})
LOCAL_ONLY_PROVIDERS = frozenset({"ollama", "vllm"})
_LOCAL_ENDPOINT_HOSTS = frozenset(
    {"localhost", "host.docker.internal", "ollama", "vllm"}
)


def local_only_requested() -> bool:
    """Return whether this process explicitly requires fail-closed local LLM use."""
    return (os.getenv("LLM_PRIVACY_MODE") or "standard").strip().lower() == (
        "local_only"
    )


@dataclass(frozen=True)
class LLMSettings:
    """
    LLM 呼叫設定。全部來自環境變數，程式碼零改動即可切換後端
    （對齊 .kiro/steering/tech.md 的「模型可抽換」要求）。

    後三個 ``*_mode`` 欄位處理各家模型的能力差異。並非所有模型都支援
    OpenAI 那套完整功能（原生 tool calling、JSON 模式、system role），
    因此保留降級路徑，並依模型名稱自動選擇合適預設值。
    """

    provider: str
    base_url: str | None
    api_key: str | None
    aws_region: str | None
    timeout_seconds: float
    max_parallel: int
    backoff_base: float
    models: dict[str, str] = field(default_factory=dict)
    #: Per unique provider/region/model request-start budget.
    rpm_limit: int = 24
    #: ``native`` 用 OpenAI tool_calls；``json`` 改以 JSON 輸出模擬工具選擇
    tool_mode: str = "native"
    #: ``native`` 用 response_format=json_object；``prompt`` 僅靠提示詞要求 JSON
    json_mode: str = "native"
    #: ``native`` 使用 system role；``merge`` 併入首個 user 訊息
    system_mode: str = "native"
    #: ``local_only`` 會拒絕任何雲端 provider 或未允許的 endpoint。
    privacy_mode: str = "standard"

    def model_for(self, stage: str) -> str:
        """
        取得某階段使用的模型名稱。

        stage 可為 ``intent`` / ``writer`` / ``writer_keypages`` /
        ``mailer`` / ``chart``；查不到時回退 ``default``。
        """
        return self.models.get(stage) or self.models["default"]


@dataclass(frozen=True)
class GenerationSettings:
    """Deadline-aware delivery policy loaded from environment variables."""

    policy: str
    deadline_seconds: float
    render_reserve_seconds: float
    repair_escalate_after: int
    default_content_pages: int
    max_content_pages: int


GENERATION_POLICIES = frozenset({"strict", "required"})


def load_generation_settings() -> GenerationSettings:
    """Load required-output controls without coupling them to one LLM backend."""
    policy = (os.getenv("GENERATION_POLICY") or "required").strip().lower()

    if policy not in GENERATION_POLICIES:
        raise ValueError(
            "環境變數 GENERATION_POLICY 只能是 "
            f"{sorted(GENERATION_POLICIES)} 之一，實際為 {policy!r}"
        )

    deadline_seconds = _read_float("GENERATION_DEADLINE_SECONDS", 1500.0)
    render_reserve_seconds = _read_float(
        "GENERATION_RENDER_RESERVE_SECONDS", 240.0
    )

    if deadline_seconds <= 0:
        raise ValueError("GENERATION_DEADLINE_SECONDS 必須大於 0")

    if render_reserve_seconds < 0:
        raise ValueError("GENERATION_RENDER_RESERVE_SECONDS 不可小於 0")

    if render_reserve_seconds >= deadline_seconds:
        raise ValueError(
            "GENERATION_RENDER_RESERVE_SECONDS 必須小於 "
            "GENERATION_DEADLINE_SECONDS"
        )

    # Product policy has an absolute 15-content-page ceiling. Deployments may
    # lower it, never raise it. The default is also clamped to that limit.
    max_content_pages = min(
        15,
        _read_int("GENERATION_MAX_CONTENT_PAGES", 15, minimum=1),
    )
    default_content_pages = min(
        max_content_pages,
        _read_int("GENERATION_DEFAULT_CONTENT_PAGES", 8, minimum=1),
    )

    return GenerationSettings(
        policy=policy,
        deadline_seconds=deadline_seconds,
        render_reserve_seconds=render_reserve_seconds,
        repair_escalate_after=_read_int(
            "LLM_REPAIR_ESCALATE_AFTER", 2, minimum=1
        ),
        default_content_pages=default_content_pages,
        max_content_pages=max_content_pages,
    )


def _read_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"環境變數 {name} 必須是整數") from error

    # 防禦下限：避免現場調參把平行度設成 0 導致整條管線停擺。
    return max(value, minimum)


def _read_float(name: str, default: float) -> float:
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    try:
        return float(raw)
    except ValueError as error:
        raise ValueError(f"環境變數 {name} 必須是數字") from error


def endpoint_host(base_url: str | None) -> str | None:
    """Return only the endpoint hostname; never expose URL credentials or paths."""
    if not base_url:
        return None

    from urllib.parse import urlsplit

    return urlsplit(base_url).hostname


def _pin_local_only_endpoint(provider: str, base_url: str | None) -> str:
    """Validate and pin a local endpoint so the SDK cannot re-resolve DNS."""
    import ipaddress
    import socket
    from urllib.parse import urlsplit, urlunsplit

    if provider not in LOCAL_ONLY_PROVIDERS:
        raise ValueError(
            "LLM_PRIVACY_MODE=local_only 僅允許 "
            f"{sorted(LOCAL_ONLY_PROVIDERS)}，實際為 {provider!r}"
        )
    if not base_url:
        raise ValueError("LLM_PRIVACY_MODE=local_only 必須設定 LLM_BASE_URL")

    parsed = urlsplit(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("LLM_BASE_URL 必須是含 http/https scheme 的完整 URL")
    if parsed.username or parsed.password:
        raise ValueError("LLM_BASE_URL 不可內嵌帳號或密碼")
    if parsed.query or parsed.fragment:
        raise ValueError("local_only 的 LLM_BASE_URL 不可含 query 或 fragment")

    allowlist = {
        item.strip().lower()
        for item in (os.getenv("LLM_LOCAL_ENDPOINT_ALLOWLIST") or "").split(",")
        if item.strip()
    }

    def is_safe_address(value: str) -> bool:
        address = ipaddress.ip_address(value)
        if address.is_loopback:
            return True
        return bool(
            not address.is_unspecified
            and not address.is_multicast
            and not address.is_reserved
            and (address.is_private or address.is_link_local)
        )

    try:
        if not is_safe_address(host):
            raise ValueError(f"local_only 拒絕公開 LLM endpoint：{host!r}")
        # Literal IPs cannot be DNS-rebound.
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, "", "")
        )
    except ValueError:
        allowed_hostnames = _LOCAL_ENDPOINT_HOSTS | allowlist
        if host not in allowed_hostnames:
            raise ValueError(
                "local_only 拒絕未列入 LLM_LOCAL_ENDPOINT_ALLOWLIST 的 hostname："
                f"{host!r}"
            ) from None

    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                host, parsed.port, type=socket.SOCK_STREAM
            )
        }
    except OSError as error:
        raise ValueError(
            f"local_only 無法解析 allowlist hostname：{host!r}"
        ) from error
    if not addresses or not all(is_safe_address(item) for item in addresses):
        raise ValueError(
            f"local_only hostname 解析到非私有位址：{host!r}"
        )
    if parsed.scheme == "https":
        raise ValueError(
            "local_only 的 HTTPS hostname 無法在 DNS pinning 後安全驗證；"
            "請使用受控 HTTP 內網端點或憑證涵蓋的私有 IP"
        )

    # Use a verified address in the actual SDK URL. The OpenAI client therefore
    # cannot perform a second DNS lookup and escape to a public destination.
    pinned_host = sorted(addresses, key=lambda value: (":" in value, value))[0]
    authority_host = f"[{pinned_host}]" if ":" in pinned_host else pinned_host
    authority = (
        f"{authority_host}:{parsed.port}"
        if parsed.port is not None
        else authority_host
    )
    return urlunsplit((parsed.scheme, authority, parsed.path, "", ""))


def validate_llm_settings(settings: LLMSettings) -> LLMSettings:
    """Validate injected settings too, so callers cannot bypass privacy mode."""
    environment_mode = (
        os.getenv("LLM_PRIVACY_MODE") or "standard"
    ).strip().lower()
    if environment_mode == "local_only" and settings.privacy_mode != "local_only":
        raise ValueError(
            "程序環境已啟用 LLM_PRIVACY_MODE=local_only；"
            "禁止注入 standard settings 降級隱私政策"
        )
    if settings.privacy_mode not in LLM_PRIVACY_MODES:
        raise ValueError(
            "privacy_mode 只能是 "
            f"{sorted(LLM_PRIVACY_MODES)}，實際為 {settings.privacy_mode!r}"
        )
    if settings.privacy_mode == "local_only":
        pinned_url = _pin_local_only_endpoint(
            settings.provider, settings.base_url
        )
        if pinned_url != settings.base_url:
            return replace(settings, base_url=pinned_url)
    return settings


def load_llm_settings() -> LLMSettings:
    """
    從環境變數組裝 LLM 設定。

    | 環境變數 | 用途 | 預設 |
    |---|---|---|
    | ``LLM_PROVIDER`` | ``openai`` / ``google`` / ``bedrock`` / ``ollama`` / ``vllm`` / ``litellm`` | ``openai`` |
    | ``LLM_BASE_URL`` | OpenAI 相容端點；provider 有捷徑時可省略 | 依 provider |
    | ``LLM_LOCAL_API_KEY`` | 僅供受控內網 vLLM 認證；Ollama 永不帶 credential | 空 |
    | ``LLM_MODEL_DEFAULT`` | 全案預設模型 | ``gpt-4o-mini`` |
    | ``LLM_MODEL_INTENT`` / ``LLM_MODEL_WRITER`` / ``LLM_MODEL_WRITER_FALLBACK`` / ``LLM_MODEL_WRITER_KEYPAGES`` / ``LLM_MODEL_MAILER`` / ``LLM_MODEL_CHART`` / ``LLM_MODEL_REVIEWER`` | per-stage 模型路由 | 回退 default |
    | ``LLM_MAX_PARALLEL`` | 敘事平行度 | 16（下限 4） |
    | ``LLM_BACKOFF_BASE`` | 重試退避基數（秒） | 1.0 |
    | ``LLM_TOOL_MODE`` | ``native`` / ``json`` | 依模型自動判斷 |
    | ``LLM_JSON_MODE`` | ``native`` / ``prompt`` | 依模型自動判斷 |
    | ``LLM_SYSTEM_MODE`` | ``native`` / ``merge`` | 依模型自動判斷 |
    | ``LLM_PRIVACY_MODE`` | ``standard`` / ``local_only`` | ``standard`` |
    | ``LLM_LOCAL_ENDPOINT_ALLOWLIST`` | local_only 額外允許的內網 hostname（逗號分隔） | 空 |
    | ``AWS_REGION`` | Bedrock 用，必須顯式指定 | 無預設 |
    """
    provider = (os.getenv("LLM_PROVIDER") or "openai").strip().lower()

    base_url = os.getenv("LLM_BASE_URL") or PROVIDER_BASE_URLS.get(provider)
    privacy_mode = _read_mode(
        "LLM_PRIVACY_MODE", "standard", set(LLM_PRIVACY_MODES)
    )
    if privacy_mode == "local_only":
        base_url = _pin_local_only_endpoint(provider, base_url)

    # 地端 provider 不得誤用 LLM_API_KEY、OPENAI/Google key 或 key file。
    # Ollama 固定無 credential；受控內網 vLLM 若需認證，使用獨立變數。
    if provider == "ollama":
        api_key = None
    elif provider == "vllm":
        local_key = os.getenv("LLM_LOCAL_API_KEY")
        api_key = local_key.strip() if local_key and local_key.strip() else None
    else:
        api_key = load_credential(
            "LLM_API_KEY",
            "OPENAI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            required=False,
        )

    models = {
        "default": os.getenv("LLM_MODEL_DEFAULT") or "gpt-4o-mini",
    }

    for stage, env_name in (
        ("intent", "LLM_MODEL_INTENT"),
        ("writer", "LLM_MODEL_WRITER"),
        ("writer_fallback", "LLM_MODEL_WRITER_FALLBACK"),
        ("writer_keypages", "LLM_MODEL_WRITER_KEYPAGES"),
        ("mailer", "LLM_MODEL_MAILER"),
        ("chart", "LLM_MODEL_CHART"),
        ("reviewer", "LLM_MODEL_REVIEWER"),
    ):
        value = os.getenv(env_name)

        if value and value.strip():
            models[stage] = value.strip()

    capability_defaults = _capability_defaults(models["default"])

    return LLMSettings(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        # 所有 AWS SDK client 必須顯式指定 region，禁止依賴環境預設。
        aws_region=os.getenv("AWS_REGION"),
        timeout_seconds=_read_float("LLM_TIMEOUT_SECONDS", 60.0),
        max_parallel=_read_int("LLM_MAX_PARALLEL", 16, minimum=4),
        backoff_base=_read_float("LLM_BACKOFF_BASE", 1.0),
        models=models,
        rpm_limit=_read_int("LLM_RPM_LIMIT", 24, minimum=1),
        tool_mode=_read_mode(
            "LLM_TOOL_MODE", capability_defaults["tool_mode"], {"native", "json"}
        ),
        json_mode=_read_mode(
            "LLM_JSON_MODE", capability_defaults["json_mode"], {"native", "prompt"}
        ),
        system_mode=_read_mode(
            "LLM_SYSTEM_MODE",
            capability_defaults["system_mode"],
            {"native", "merge"},
        ),
        privacy_mode=privacy_mode,
    )


def _read_mode(name: str, default: str, allowed: set[str]) -> str:
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    value = raw.strip().lower()

    if value not in allowed:
        raise ValueError(
            f"環境變數 {name} 只能是 {sorted(allowed)} 之一，實際為 {value!r}"
        )

    return value


def _capability_defaults(model: str) -> dict[str, str]:
    """
    依模型名稱推斷能力預設值。

    Gemma 系列（含透過 Gemini API 代管的 gemma-*）與 OpenAI 的差異：

    - **無原生 tool calling**：Google 官方的 Gemma function calling 指南是教你
      用提示詞構造工具呼叫，而非呼叫原生 tool API，因此預設走 ``json`` 模擬。
    - **無 JSON 模式**：OpenAI 相容層的 ``response_format=json_object``
      對 Gemma 不保證支援，預設改以提示詞要求 JSON。
    - **無 system role**：Gemma 的對話模板沒有 system turn，預設把 system
      提示詞併入首個 user 訊息。

    上述判斷可用 ``LLM_TOOL_MODE`` / ``LLM_JSON_MODE`` / ``LLM_SYSTEM_MODE``
    覆寫。若實測發現某個模型其實支援，改環境變數即可，不需動程式碼。
    """
    normalized = model.lower()

    if "gemma" in normalized:
        return {
            "tool_mode": "json",
            "json_mode": "prompt",
            "system_mode": "merge",
        }

    return {
        "tool_mode": "native",
        "json_mode": "native",
        "system_mode": "native",
    }
