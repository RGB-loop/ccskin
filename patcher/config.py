"""项目配置加载: 优先 config.toml,缺失时回退 config.example.toml。

config.toml 在 .gitignore 里(放 api_key 等私有配置);
PATCHER_LLM_API_KEY 环境变量由 llm.py 读取,不注入 cfg,
避免 skin 向导写回 config.toml 时把密钥落盘。
"""
import pathlib

from . import tomlmini


def load(project_root: pathlib.Path) -> dict:
    path = project_root / "config.toml"
    if not path.exists():
        path = project_root / "config.example.toml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return tomlmini.loads(f.read())
