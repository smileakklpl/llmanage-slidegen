"""
LLM 呼叫統一介面
=================
對應開發規格書 §4.3 的 `llm` 模組與 A1「模型抽換層」。

對外只暴露兩個進入點：

- :func:`complete_json`  ── 要求 LLM 回傳符合 schema 的 JSON
- :func:`complete_tool_call` ── 要求 LLM 從工具白名單中挑一個並填參數

設計約束：
1. 呼叫端（各 Agent）不得知道自己在跟哪家 LLM 說話，
   切換雲端／地端只透過環境變數，程式碼零改動。
2. LLM 回傳的內容一律經 schema 驗證後才交給下游，
   驗證失敗就重試；重試用盡則拋錯，不讓半成品流入管線。
3. 本模組不做任何數值計算，也不允許呼叫端把數值塞進 prompt 後
   要求 LLM 算數 —— 數字一律由 metric_engine 產生。
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from . import config


logger = logging.getLogger(__name__)

#: 允許的重試次數（不含第一次呼叫）。
DEFAULT_MAX_RETRIES = 2


class LLMError(RuntimeError):
    """LLM 呼叫或回應驗證失敗。"""


class SchemaValidationError(LLMError):
    """LLM 回傳的 JSON 不符合要求的 schema。"""


@dataclass
class ToolCall:
    """LLM 選擇的工具與參數。參數尚未經業務層驗證。"""

    name: str
    arguments: dict[str, Any]


# ---------------------------------------------------------------------------
# 輕量 schema 驗證
# ---------------------------------------------------------------------------
# 刻意不引入 jsonschema 依賴：這裡只需要驗證 tool schema 用得到的子集
# （type / properties / required / items / enum），保持依賴精簡。
# ---------------------------------------------------------------------------
_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate_against_schema(
    payload: Any,
    schema: dict[str, Any],
    path: str = "$",
) -> None:
    """
    以最小可用的 JSON Schema 子集驗證 payload。

    Raises:
        SchemaValidationError: 型別、必填欄位或 enum 不符。
    """
    expected_type = schema.get("type")

    if expected_type:
        python_type = _TYPE_MAP.get(expected_type)

        if python_type is None:
            raise SchemaValidationError(
                f"{path}: schema 使用了不支援的 type {expected_type!r}"
            )

        # bool 是 int 的子類，避免 True 被當成合法的 number。
        if expected_type in {"number", "integer"} and isinstance(payload, bool):
            raise SchemaValidationError(
                f"{path}: 期望 {expected_type}，實際得到 boolean"
            )

        if not isinstance(payload, python_type):
            raise SchemaValidationError(
                f"{path}: 期望 {expected_type}，"
                f"實際得到 {type(payload).__name__}"
            )

    enum_values = schema.get("enum")

    if enum_values is not None and payload not in enum_values:
        raise SchemaValidationError(
            f"{path}: 值不在允許清單內，允許值為 {enum_values}"
        )

    if expected_type == "object":
        properties: dict[str, Any] = schema.get("properties", {})

        for required_key in schema.get("required", []):
            if required_key not in payload:
                raise SchemaValidationError(
                    f"{path}: 缺少必填欄位 {required_key!r}"
                )

        for key, value in payload.items():
            sub_schema = properties.get(key)

            if sub_schema is not None:
                validate_against_schema(value, sub_schema, f"{path}.{key}")

    if expected_type == "array":
        item_schema = schema.get("items")

        if item_schema is not None:
            for index, item in enumerate(payload):
                validate_against_schema(item, item_schema, f"{path}[{index}]")


# ---------------------------------------------------------------------------
# 後端呼叫
# ---------------------------------------------------------------------------
def _strip_code_fence(text: str) -> str:
    """去掉 ```json ... ``` 外殼。"""
    stripped = text.strip()

    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()[1:]

    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]

    return "\n".join(lines).strip()


def parse_json_response(text: str) -> Any:
    """
    從模型回覆中解析出第一個完整的 JSON 值。

    真實模型（尤其是沒有 JSON 模式的開源模型）常見三種雜訊：

    1. 包 markdown code fence
    2. JSON 前面有一段說明文字
    3. JSON **後面**還接了說明文字 —— 此時直接 ``json.loads`` 會拋
       "Extra data"，因此改用 ``raw_decode`` 只取第一個完整值

    Raises:
        SchemaValidationError: 找不到可解析的 JSON。
    """
    stripped = _strip_code_fence(text)
    decoder = json.JSONDecoder()

    # 從每個可能的起點嘗試 raw_decode，取第一個成功的完整 JSON 值。
    for index, char in enumerate(stripped):
        if char not in "{[":
            continue

        try:
            payload, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue

        return payload

    raise SchemaValidationError(
        f"模型回覆中找不到可解析的 JSON。回覆開頭：{stripped[:120]!r}"
    )


def _adapt_messages(
    messages: list[dict[str, str]],
    system_mode: str,
) -> list[dict[str, str]]:
    """
    依模型能力調整訊息結構。

    ``system_mode="merge"``：把 system 訊息併入首個 user 訊息。
    Gemma 的對話模板沒有 system turn，直接送 system role 會被忽略或報錯，
    而我們的 system 提示詞承載了「不可寫出數字」這類關鍵約束，不能被丟掉。
    """
    if system_mode == "native":
        return messages

    system_parts = [
        message["content"]
        for message in messages
        if message["role"] == "system"
    ]

    others = [message for message in messages if message["role"] != "system"]

    if not system_parts:
        return others

    preamble = "\n\n".join(system_parts)

    for index, message in enumerate(others):
        if message["role"] == "user":
            merged = dict(message)
            merged["content"] = f"{preamble}\n\n---\n\n{message['content']}"
            return [*others[:index], merged, *others[index + 1 :]]

    # 沒有 user 訊息時（理論上不會發生），退化成單一 user 訊息。
    return [{"role": "user", "content": preamble}, *others]


def _call_openai_compatible(
    settings: config.LLMSettings,
    model: str,
    messages: list[dict[str, str]],
    tools: Sequence[dict[str, Any]] | None,
    temperature: float,
) -> dict[str, Any]:
    """
    呼叫 OpenAI 相容端點（OpenAI / vLLM / Ollama 皆走此路徑）。

    回傳原始 message dict，交由上層解析成 JSON 或 ToolCall。
    """
    try:
        from openai import OpenAI
    except ImportError as error:
        raise LLMError(
            "尚未安裝 openai 套件。請執行 "
            "python -m pip install openai"
        ) from error

    if not settings.api_key and settings.provider == "openai":
        raise config.CredentialNotFoundError(
            "LLM_PROVIDER=openai 需要 API 金鑰。請設定 LLM_API_KEY "
            f"或將金鑰寫入 {config.DEFAULT_API_KEY_FILE}"
        )

    client = OpenAI(
        # 地端 vLLM／Ollama 通常不驗證金鑰，但 SDK 要求非空值。
        api_key=settings.api_key or "not-required",
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
    )

    request: dict[str, Any] = {
        "model": model,
        "messages": _adapt_messages(messages, settings.system_mode),
        "temperature": temperature,
    }

    if tools:
        request["tools"] = [
            {"type": "function", "function": schema} for schema in tools
        ]
        request["tool_choice"] = "required"
    elif settings.json_mode == "native":
        # 部分模型（如 Gemma）不支援 response_format，此時改由提示詞要求 JSON。
        request["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**request)
    message = response.choices[0].message

    return {
        "content": message.content,
        "tool_calls": [
            {
                "name": call.function.name,
                "arguments": call.function.arguments,
            }
            for call in (message.tool_calls or [])
        ],
    }


def _call_bedrock(
    settings: config.LLMSettings,
    model: str,
    messages: list[dict[str, str]],
    tools: Sequence[dict[str, Any]] | None,
    temperature: float,
) -> dict[str, Any]:
    """
    呼叫 AWS Bedrock Converse API。

    Bedrock 走 SDK 簽章，不需要 API key，但必須顯式指定 region
    （對齊 steering：禁止依賴環境預設 region）。
    """
    try:
        import boto3
    except ImportError as error:
        raise LLMError(
            "尚未安裝 boto3 套件。請執行 python -m pip install boto3"
        ) from error

    if not settings.aws_region:
        raise config.CredentialNotFoundError(
            "LLM_PROVIDER=bedrock 需要顯式指定 AWS_REGION 環境變數"
        )

    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)

    system_prompts = [
        {"text": message["content"]}
        for message in messages
        if message["role"] == "system"
    ]

    converse_messages = [
        {"role": message["role"], "content": [{"text": message["content"]}]}
        for message in messages
        if message["role"] != "system"
    ]

    request: dict[str, Any] = {
        "modelId": model,
        "messages": converse_messages,
        "inferenceConfig": {"temperature": temperature},
    }

    if system_prompts:
        request["system"] = system_prompts

    if tools:
        request["toolConfig"] = {
            "tools": [
                {
                    "toolSpec": {
                        "name": schema["name"],
                        "description": schema.get("description", ""),
                        "inputSchema": {"json": schema["parameters"]},
                    }
                }
                for schema in tools
            ],
            "toolChoice": {"any": {}},
        }

    response = client.converse(**request)
    content_blocks = response["output"]["message"]["content"]

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in content_blocks:
        if "text" in block:
            text_parts.append(block["text"])

        if "toolUse" in block:
            tool_use = block["toolUse"]
            tool_calls.append(
                {
                    "name": tool_use["name"],
                    # Bedrock 已回傳 dict，統一成字串交由上層 json.loads。
                    "arguments": json.dumps(tool_use["input"]),
                }
            )

    return {"content": "".join(text_parts), "tool_calls": tool_calls}


def _dispatch_backend(
    settings: config.LLMSettings,
) -> Callable[..., dict[str, Any]]:
    if settings.provider == "bedrock":
        return _call_bedrock

    if settings.provider in config.OPENAI_COMPATIBLE_PROVIDERS:
        return _call_openai_compatible

    raise LLMError(
        f"不支援的 LLM_PROVIDER: {settings.provider!r}。"
        f"可用值：bedrock / {' / '.join(sorted(config.OPENAI_COMPATIBLE_PROVIDERS))}"
    )


def _with_retry(
    operation: Callable[[], Any],
    max_retries: int,
    backoff_base: float,
) -> Any:
    """指數退避重試。schema 驗證失敗也重試，因為多半是模型當次輸出不穩。"""
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return operation()
        except (LLMError, json.JSONDecodeError) as error:
            last_error = error

            if attempt == max_retries:
                break

            # 加入抖動，避免多個平行呼叫同時重試造成尖峰。
            delay = backoff_base * (2**attempt) + random.uniform(0, 0.3)
            logger.warning(
                "LLM 呼叫失敗（第 %d/%d 次），%.1f 秒後重試：%s",
                attempt + 1,
                max_retries + 1,
                delay,
                error,
            )
            time.sleep(delay)

    raise LLMError(f"LLM 呼叫重試 {max_retries + 1} 次仍失敗：{last_error}")


# ---------------------------------------------------------------------------
# 對外介面
# ---------------------------------------------------------------------------
def complete_json(
    prompt: str,
    schema: dict[str, Any],
    *,
    system_prompt: str | None = None,
    stage: str = "default",
    temperature: float = 0.0,
    max_retries: int = DEFAULT_MAX_RETRIES,
    settings: config.LLMSettings | None = None,
) -> Any:
    """
    要求 LLM 回傳符合 ``schema`` 的 JSON，驗證通過後回傳解析結果。

    Args:
        prompt: 使用者訊息內容。
        schema: JSON Schema（支援 type/properties/required/items/enum 子集）。
        system_prompt: 系統提示詞。
        stage: 模型路由階段名稱，見 :meth:`config.LLMSettings.model_for`。
        temperature: 預設 0，決策類任務要求可重現。
        max_retries: 重試次數。
        settings: 覆寫設定，測試時可注入。

    Raises:
        LLMError: 重試用盡仍無法取得合法回應。
    """
    resolved = settings or config.load_llm_settings()
    backend = _dispatch_backend(resolved)
    model = resolved.model_for(stage)

    messages: list[dict[str, str]] = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    user_content = prompt

    if resolved.json_mode == "prompt":
        # 沒有 response_format 可用時，改由提示詞約束輸出格式，
        # 並附上 schema 讓模型知道要填哪些欄位。
        user_content = (
            f"{prompt}\n\n---\n\n"
            "## 輸出要求\n"
            "只輸出符合下列 JSON Schema 的 JSON，"
            "不要加任何說明文字、不要用 markdown 圍籬。\n"
            f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
        )

    messages.append({"role": "user", "content": user_content})

    def operation() -> Any:
        raw = backend(resolved, model, messages, None, temperature)
        content = raw.get("content") or ""

        if not content.strip():
            raise LLMError("模型回傳空內容")

        payload = parse_json_response(content)
        validate_against_schema(payload, schema)
        return payload

    return _with_retry(operation, max_retries, resolved.backoff_base)


def complete_tool_call(
    prompt: str,
    tool_schemas: Sequence[dict[str, Any]],
    *,
    system_prompt: str | None = None,
    stage: str = "default",
    temperature: float = 0.0,
    max_retries: int = DEFAULT_MAX_RETRIES,
    settings: config.LLMSettings | None = None,
) -> ToolCall:
    """
    要求 LLM 從 ``tool_schemas`` 白名單中選一個工具並填入參數。

    回傳的 :class:`ToolCall` 只保證「工具名稱在白名單內、參數符合該工具
    的 JSON Schema」。業務層防呆（例如 metric_key 是否真的存在於
    MetricStore）由 ``chart_planner.validate_chart_plan()`` 負責，
    本函式不越權判斷。
    """
    resolved = settings or config.load_llm_settings()

    # 沒有原生 tool calling 的模型（如 Gemma），改以 JSON 輸出模擬工具選擇。
    if resolved.tool_mode == "json":
        return _emulate_tool_call(
            prompt,
            tool_schemas,
            system_prompt=system_prompt,
            stage=stage,
            temperature=temperature,
            max_retries=max_retries,
            settings=resolved,
        )

    backend = _dispatch_backend(resolved)
    model = resolved.model_for(stage)

    schema_by_name = {schema["name"]: schema for schema in tool_schemas}

    messages: list[dict[str, str]] = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    messages.append({"role": "user", "content": prompt})

    def operation() -> ToolCall:
        raw = backend(resolved, model, messages, tool_schemas, temperature)
        calls = raw.get("tool_calls") or []

        if not calls:
            raise LLMError("模型未回傳任何工具呼叫")

        first = calls[0]
        name = first["name"]
        selected = schema_by_name.get(name)

        if selected is None:
            raise SchemaValidationError(
                f"模型選了未註冊的工具 {name!r}，"
                f"可用選項：{sorted(schema_by_name)}"
            )

        arguments = first["arguments"]

        if isinstance(arguments, str):
            arguments = json.loads(arguments or "{}")

        validate_against_schema(arguments, selected["parameters"])
        return ToolCall(name=name, arguments=arguments)

    return _with_retry(operation, max_retries, resolved.backoff_base)


#: 模擬工具呼叫時要求 LLM 回傳的外層結構。
_EMULATED_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool_name": {"type": "string"},
        "arguments": {"type": "object"},
    },
    "required": ["tool_name", "arguments"],
}


def _emulate_tool_call(
    prompt: str,
    tool_schemas: Sequence[dict[str, Any]],
    *,
    system_prompt: str | None,
    stage: str,
    temperature: float,
    max_retries: int,
    settings: config.LLMSettings,
) -> ToolCall:
    """
    以 JSON 輸出模擬工具呼叫，供不支援原生 tool calling 的模型使用。

    把工具清單連同各自的參數 schema 寫進提示詞，要求模型回傳
    ``{"tool_name": ..., "arguments": {...}}``，再套用與原生路徑**完全相同**
    的白名單與 schema 驗證。

    安全性與原生路徑等價：工具名稱仍只能來自白名單，參數仍經 schema 驗證，
    模型無法藉此執行任何未註冊的程式碼路徑。
    """
    schema_by_name = {schema["name"]: schema for schema in tool_schemas}

    catalog = [
        {
            "tool_name": schema["name"],
            "description": schema.get("description", ""),
            "arguments_schema": schema["parameters"],
        }
        for schema in tool_schemas
    ]

    instruction = (
        "你必須從下列工具清單中選出**恰好一個**工具，並填入符合其 "
        "arguments_schema 的參數。\n\n"
        "## 可用工具\n"
        f"{json.dumps(catalog, ensure_ascii=False, indent=2)}\n\n"
        "## 輸出格式（只輸出 JSON，不要加說明文字或 markdown 圍籬）\n"
        '{"tool_name": "選中的工具名稱", "arguments": {"參數名": "參數值"}}'
    )

    combined_prompt = f"{prompt}\n\n---\n\n{instruction}"

    payload = complete_json(
        combined_prompt,
        _EMULATED_TOOL_SCHEMA,
        system_prompt=system_prompt,
        stage=stage,
        temperature=temperature,
        max_retries=max_retries,
        settings=settings,
    )

    name = payload["tool_name"]
    selected = schema_by_name.get(name)

    if selected is None:
        raise SchemaValidationError(
            f"模型選了未註冊的工具 {name!r}，"
            f"可用選項：{sorted(schema_by_name)}"
        )

    arguments = payload["arguments"]
    validate_against_schema(arguments, selected["parameters"])

    return ToolCall(name=name, arguments=arguments)
