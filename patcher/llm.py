"""可选的 LLM 辅助分析(OpenAI 兼容接口)。

仅在 analyze 自动发现锚点失败时使用:把上下文片段发给 LLM 请它判断
锚点在哪/结构是否变了。LLM 只给建议,绝不自动改写补丁定义。
base_url / api_key / model 在 config.toml 的 [llm] 节配置,
api_key 也可用环境变量 PATCHER_LLM_API_KEY 覆盖(推荐,key 不入库)。
"""
import json
import os
import urllib.request


def chat(cfg: dict, prompt: str, max_tokens: int = 2000) -> str:
    llm = cfg.get("llm", {})
    base_url = (llm.get("base_url") or "").rstrip("/")
    api_key = os.environ.get("PATCHER_LLM_API_KEY") or llm.get("api_key") or ""
    model = llm.get("model") or ""
    if not base_url or not model:
        raise RuntimeError("config.toml 缺少 [llm].base_url / model")
    if not api_key:
        raise RuntimeError("未配置 API key(设 PATCHER_LLM_API_KEY 或 [llm].api_key)")
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0,
            }
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


ASSIST_PROMPT = """\
背景: 我们在给 Claude Code 的 macOS 单文件二进制(Bun 内嵌 JS 源码)做\
"仅显示层"补丁: 替换启动画面的像素 logo、产品名、版本号显示。\
方法是在内嵌 JS 源码里找到锚点字符串做等长字节替换。\
上一版本的锚点如下,但在新二进制里自动定位失败了,请根据上下文片段判断:
1) 这些锚点是改名了、结构变了,还是确实不存在了?
2) 如果存在,给出新锚点的准确字节串(原样引用,不要转义);不存在就明说。

失败的锚点与上下文:
{context}

只输出结论和候选锚点清单,不要输出无关内容。"""


def assist(cfg: dict, problems: list, contexts: list) -> str:
    ctx = "\n\n".join(contexts) if contexts else "(无上下文可提取)"
    ctx += "\n\n自动检查发现的问题:\n" + "\n".join(f"- {p}" for p in problems)
    return chat(cfg, ASSIST_PROMPT.format(context=ctx))
