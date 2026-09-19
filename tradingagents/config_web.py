"""兼容旧导入路径的 Web 服务入口。"""

from tradingagents.web.app import create_web_app, run_web_server


def run_config_server(
    port: int = 8765,
    env_path=None,
    open_browser: bool = True,
) -> None:
    """启动新 Web 服务并直接打开设置页面。"""
    run_web_server(
        port=port,
        env_path=env_path,
        open_browser=open_browser,
        initial_path="/settings",
    )


__all__ = ["create_web_app", "run_config_server", "run_web_server"]
