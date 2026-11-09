"""Nana-Agent 入口。

启动顺序很重要：
1. 先确保 config.ini 存在（缺失则生成 mock 模板并提示用户修改后重启）
2. 再导入 Agent / TUI（这两个模块在导入时读取配置）
3. 最后启动 TUI；MCP 在进入界面后异步加载并刷新侧边栏
"""
from core.config import ensure_config


def main() -> None:
    config_created = ensure_config()

    from core.agent import Agent
    from tui.app import NanaApp

    NanaApp(Agent(), config_created=config_created).run()


if __name__ == "__main__":
    main()
