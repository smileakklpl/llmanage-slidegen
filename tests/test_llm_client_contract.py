"""LLM 呼叫層：三個由真實模型實跑才浮現的契約問題。

這三件事全部是 2026-07-30 首次接上 Gemini 實跑時抓到的，之前只走假 LLM
永遠不會發生——假回應天生就符合 schema、不會限流。留成測試是為了讓下次
換模型時，這幾條不必再用真實額度重新發現一次。

1. **schema 必須送進 prompt。** ``response_format={"type":"json_object"}``
   只保證「是一個 JSON」，不保證欄位名稱。實測 gemini-3.6-flash 把
   ``sections`` 寫成 ``pages``，結構完全正確但驗證後成了空清單，
   整份簡報靜默少了十頁。
2. **非必填欄位給 null 等同沒給。** 模型很常把不適用的欄位明確寫成 null；
   當成型別錯誤會讓一個合法回應重試三次後中斷管線。
3. **限流要重試。** 429 是現場 Demo 最容易遇到的失敗，一次就讓簡報生不
   出來是不能接受的。
"""

import json

import pytest

from ppt_generation.core import config, llm_client


SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "question": {"type": "string"},
        "items": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status"],
}


def _settings(**overrides):
    base = dict(
        provider="openai",
        base_url=None,
        api_key="test-key",
        aws_region=None,
        timeout_seconds=5.0,
        max_parallel=4,
        backoff_base=0.0,
        models={"default": "test-model"},
    )
    base.update(overrides)

    return config.LLMSettings(**base)


# ---------------------------------------------------------------------------
# schema 驗證
# ---------------------------------------------------------------------------
def test_null_is_accepted_for_optional_fields():
    llm_client.validate_against_schema(
        {"status": "READY", "question": None}, SCHEMA
    )


def test_null_is_still_rejected_for_required_fields():
    with pytest.raises(llm_client.SchemaValidationError):
        llm_client.validate_against_schema({"status": None}, SCHEMA)


def test_wrong_type_for_optional_field_is_still_rejected():
    """放寬只針對 null。型別真的寫錯仍要擋，否則放寬變成不驗。"""
    with pytest.raises(llm_client.SchemaValidationError):
        llm_client.validate_against_schema(
            {"status": "READY", "items": "不是陣列"}, SCHEMA
        )


# ---------------------------------------------------------------------------
# schema 進 prompt
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("json_mode", ["native", "prompt"])
def test_schema_is_sent_to_the_model_in_both_json_modes(monkeypatch, json_mode):
    seen: dict[str, object] = {}

    def fake_backend(settings, model, messages, tools, temperature):
        seen["messages"] = messages
        return {"content": json.dumps({"status": "READY"})}

    monkeypatch.setattr(llm_client, "_dispatch_backend", lambda _: fake_backend)

    llm_client.complete_json(
        "請規劃章節",
        SCHEMA,
        settings=_settings(json_mode=json_mode),
    )

    user_content = seen["messages"][-1]["content"]

    # 模型看不到 schema 就只能猜欄位名，所以兩種模式都必須附上。
    assert "items" in user_content
    assert "status" in user_content
    assert "不可改名" in user_content


# ---------------------------------------------------------------------------
# 暫時性失敗重試
# ---------------------------------------------------------------------------
class _RateLimitError(Exception):
    """模仿 openai.RateLimitError：靠類別名稱與訊息判斷，不 import openai。"""

    status_code = 429

    def __str__(self) -> str:
        return "Error code: 429 - quota exceeded. Please retry in 0.01s."


_RateLimitError.__name__ = "RateLimitError"


def test_rate_limit_is_retried_then_succeeds(monkeypatch):
    calls = {"count": 0}

    def fake_backend(settings, model, messages, tools, temperature):
        calls["count"] += 1

        if calls["count"] == 1:
            raise _RateLimitError()

        return {"content": json.dumps({"status": "READY"})}

    monkeypatch.setattr(llm_client, "_dispatch_backend", lambda _: fake_backend)

    payload = llm_client.complete_json("x", SCHEMA, settings=_settings())

    assert payload == {"status": "READY"}
    assert calls["count"] == 2


def test_non_transient_errors_are_not_retried(monkeypatch):
    """設定錯誤重試三次只是把同一個錯誤延後三倍時間才報出來。"""
    calls = {"count": 0}

    def fake_backend(settings, model, messages, tools, temperature):
        calls["count"] += 1
        raise ValueError("模型名稱打錯了")

    monkeypatch.setattr(llm_client, "_dispatch_backend", lambda _: fake_backend)

    with pytest.raises(ValueError):
        llm_client.complete_json("x", SCHEMA, settings=_settings())

    assert calls["count"] == 1


def test_retry_after_hint_is_honoured():
    assert llm_client._retry_after(_RateLimitError()) == pytest.approx(0.01)
    assert llm_client._retry_after(ValueError("沒有提示")) is None
