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
from dataclasses import dataclass, field
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
    #: ``native`` 用 OpenAI tool_calls；``json`` 改以 JSON 輸出模擬工具選擇
    tool_mode: str = "native"
    #: ``native`` 用 response_format=json_object；``prompt`` 僅靠提示詞要求 JSON
    json_mode: str = "native"
    #: ``native`` 使用 system role；``merge`` 併入首個 user 訊息
    system_mode: str = "native"

    def model_for(self, stage: str) -> str:
        """
        取得某階段使用的模型名稱。

        stage 可為 ``intent`` / ``writer`` / ``writer_keypages`` /
        ``mailer`` / ``chart``；查不到時回退 ``default``。
        """
        return self.models.get(stage) or self.models["default"]


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


def load_llm_settings() -> LLMSettings:
    """
    從環境變數組裝 LLM 設定。

    | 環境變數 | 用途 | 預設 |
    |---|---|---|
    | ``LLM_PROVIDER`` | ``openai`` / ``google`` / ``bedrock`` / ``ollama`` / ``vllm`` / ``litellm`` | ``openai`` |
    | ``LLM_BASE_URL`` | OpenAI 相容端點；provider 有捷徑時可省略 | 依 provider |
    | ``LLM_MODEL_DEFAULT`` | 全案預設模型 | ``gpt-4o-mini`` |
    | ``LLM_MODEL_INTENT`` / ``LLM_MODEL_WRITER`` / ``LLM_MODEL_WRITER_KEYPAGES`` / ``LLM_MODEL_MAILER`` / ``LLM_MODEL_CHART`` / ``LLM_MODEL_REVIEWER`` | per-stage 模型路由 | 回退 default |
    | ``LLM_MAX_PARALLEL`` | 敘事平行度 | 16（下限 4） |
    | ``LLM_BACKOFF_BASE`` | 重試退避基數（秒） | 1.0 |
    | ``LLM_TOOL_MODE`` | ``native`` / ``json`` | 依模型自動判斷 |
    | ``LLM_JSON_MODE`` | ``native`` / ``prompt`` | 依模型自動判斷 |
    | ``LLM_SYSTEM_MODE`` | ``native`` / ``merge`` | 依模型自動判斷 |
    | ``AWS_REGION`` | Bedrock 用，必須顯式指定 | 無預設 |
    """
    provider = (os.getenv("LLM_PROVIDER") or "openai").strip().lower()

    base_url = os.getenv("LLM_BASE_URL") or PROVIDER_BASE_URLS.get(provider)

    # Bedrock 走 AWS SDK 簽章，不需要 API key；其餘 provider 才需要。
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
