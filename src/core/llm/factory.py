"""Provider 工廠 — 由名稱決定要載哪一家 adapter。

這支原本住在 `evalh/harness.py`。那是錯的位置：`pipeline.py` 是產品碼，
卻得 `from evalh.harness import load_provider` 才跑得起來，形成
`src → evalh → src` 的循環依賴——量測骨架反而變成產品的必要相依。
副作用是 README 寫的 `python src/pipeline.py` 直接 ModuleNotFoundError，
因為那樣跑的時候 repo root 不在 sys.path 上，`evalh` 根本找不到。

provider 選擇是產品行為（使用者用 --provider 決定打哪個模型），
不是量測行為，所以歸屬 `llm/`。evalh 與 tools 改成單向往這裡拿。

各 adapter 都是延遲 import：boto3 只有 bedrock 需要，
沒裝也不該害 mock 路徑起不來——verify_all 全走 mock，這點很重要。
"""

from typing import Optional


def load_provider(name: str, model: Optional[str], num_ctx: Optional[int] = None):
    if name == "mock":
        from llm.mock import MockProvider

        return MockProvider()
    if name == "ollama":
        from llm.ollama import OllamaProvider

        # num_ctx 要能隨模型調：各模型上限不同（gemma2 系列就是 8192），
        # 開超過上限 Ollama 不會報錯，只會靜默截到上限——正是截斷警告要抓的情形。
        kw = {"num_ctx": num_ctx} if num_ctx else {}
        return OllamaProvider(model=model or "qwen2.5:14b", **kw)
    if name == "bedrock":
        from llm.bedrock import BedrockProvider

        return BedrockProvider(model=model or "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    raise ValueError(f"未知 provider: {name}")
