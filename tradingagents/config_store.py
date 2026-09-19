"""本地 Web 配置页与命令行共用的环境变量存储。"""

from __future__ import annotations

import os
import re
import stat
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values, find_dotenv


class ConfigValidationError(ValueError):
    """表示配置请求中包含不受支持或无效的值。"""


@dataclass(frozen=True)
class ConfigField:
    """描述一个允许通过配置页管理的环境变量。"""

    name: str
    label: str
    input_type: str = "password"
    secret: bool = True
    description: str = ""
    placeholder: str = ""
    options: tuple[tuple[str, str], ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None


@dataclass(frozen=True)
class ConfigGroup:
    """配置页中的一组相关字段。"""

    group_id: str
    title: str
    description: str
    fields: tuple[ConfigField, ...]


LLM_PROVIDER_OPTIONS = (
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("google", "Google Gemini"),
    ("azure", "Azure OpenAI"),
    ("bedrock", "AWS Bedrock"),
    ("xai", "xAI"),
    ("deepseek", "DeepSeek"),
    ("qwen", "Qwen 国际站"),
    ("qwen-cn", "Qwen 中国站"),
    ("glm", "GLM 国际站"),
    ("glm-cn", "GLM 中国站"),
    ("minimax", "MiniMax 国际站"),
    ("minimax-cn", "MiniMax 中国站"),
    ("openrouter", "OpenRouter"),
    ("mistral", "Mistral"),
    ("kimi", "Kimi / Moonshot"),
    ("groq", "Groq"),
    ("nvidia", "NVIDIA NIM"),
    ("ollama", "Ollama"),
    ("openai_compatible", "OpenAI 兼容接口"),
)


CONFIG_GROUPS = (
    ConfigGroup(
        "news",
        "新闻与数据源",
        "管理加密新闻、项目事件、宏观信息和可选的 X 数据凭证。",
        (
            ConfigField("AICOIN_ACCESS_KEY_ID", "AiCoin Access Key ID", description="与 Access Secret 配套使用。"),
            ConfigField("AICOIN_ACCESS_SECRET", "AiCoin Access Secret", description="用于中文新闻与 X 信息代理。"),
            ConfigField("COINDESK_API_KEY", "CoinDesk Data API Key", description="未配置时自动使用可用的 CoinDesk RSS。"),
            ConfigField(
                "COINGECKO_API_KEY",
                "CoinGecko API Key",
                description="可选；未配置时尝试 Keyless 公共接口，配置后按下方套餐类型鉴权。",
            ),
            ConfigField(
                "COINGECKO_API_PLAN",
                "CoinGecko API 套餐",
                input_type="select",
                secret=False,
                description="Demo 使用公共域名，Pro 使用 Pro API 域名。",
                options=(("demo", "Demo / Keyless（推荐）"), ("pro", "Pro")),
            ),
            ConfigField("ROOTDATA_API_KEY", "RootData API Key", description="用于项目基本面与融资事件。"),
            ConfigField(
                "TRADINGAGENTS_ROOTDATA_FALLBACK_MODE",
                "RootData 无 Key 降级模式",
                input_type="select",
                secret=False,
                description=(
                    "配置 API Key 时始终优先使用 API；无 Key 时默认聚合项目官方来源，"
                    "但不包含 RootData 专有的团队、融资关系和指数。"
                ),
                options=(
                    ("official_sources", "项目官方来源聚合（推荐）"),
                    ("manual_link", "仅提供 RootData 手动核对链接"),
                    ("disabled", "禁用项目基本面降级"),
                ),
            ),
            ConfigField("JIN10_API_URL", "金十授权 API 地址", input_type="url", secret=False, description="仅填写已获授权的完整 HTTP(S) 接口地址。", placeholder="https://..."),
            ConfigField("JIN10_API_KEY", "金十 API Key", description="与金十授权 API 地址配套使用。"),
            ConfigField("X_BEARER_TOKEN", "X Bearer Token", description="可选；用于原生 X API 数据。"),
            ConfigField("FRED_API_KEY", "FRED API Key", description="用于美联储宏观时间序列。"),
            ConfigField("ALPHA_VANTAGE_API_KEY", "Alpha Vantage API Key", description="用于可选的股票行情与新闻数据。"),
        ),
    ),
    ConfigGroup(
        "models",
        "大模型凭证",
        "按实际使用的供应商填写。未填写的密钥不会创建空配置。",
        (
            ConfigField("OPENAI_API_KEY", "OpenAI API Key"),
            ConfigField("ANTHROPIC_API_KEY", "Anthropic API Key"),
            ConfigField("GOOGLE_API_KEY", "Google Gemini API Key"),
            ConfigField("XAI_API_KEY", "xAI API Key"),
            ConfigField("DEEPSEEK_API_KEY", "DeepSeek API Key"),
            ConfigField("DASHSCOPE_API_KEY", "Qwen 国际站 API Key"),
            ConfigField("DASHSCOPE_CN_API_KEY", "Qwen 中国站 API Key"),
            ConfigField("ZHIPU_API_KEY", "GLM 国际站 API Key"),
            ConfigField("ZHIPU_CN_API_KEY", "GLM 中国站 API Key"),
            ConfigField("MINIMAX_API_KEY", "MiniMax 国际站 API Key"),
            ConfigField("MINIMAX_CN_API_KEY", "MiniMax 中国站 API Key"),
            ConfigField("OPENROUTER_API_KEY", "OpenRouter API Key"),
            ConfigField("MISTRAL_API_KEY", "Mistral API Key"),
            ConfigField("MOONSHOT_API_KEY", "Kimi / Moonshot API Key"),
            ConfigField("GROQ_API_KEY", "Groq API Key"),
            ConfigField("NVIDIA_API_KEY", "NVIDIA NIM API Key"),
            ConfigField("OPENAI_COMPATIBLE_API_KEY", "OpenAI 兼容接口密钥", description="本地无鉴权服务可留空。"),
            ConfigField("AZURE_OPENAI_API_KEY", "Azure OpenAI API Key"),
            ConfigField("AZURE_OPENAI_ENDPOINT", "Azure OpenAI Endpoint", input_type="url", secret=False, placeholder="https://...openai.azure.com/"),
            ConfigField("AZURE_OPENAI_DEPLOYMENT_NAME", "Azure 部署名称", input_type="text", secret=False),
            ConfigField("OPENAI_API_VERSION", "Azure OpenAI API 版本", input_type="text", secret=False, placeholder="2025-03-01-preview"),
            ConfigField("AWS_BEARER_TOKEN_BEDROCK", "AWS Bedrock Bearer Token", description="不在此页面保存 AWS Access Key。"),
            ConfigField("AWS_DEFAULT_REGION", "AWS 默认区域", input_type="text", secret=False, placeholder="us-west-2"),
            ConfigField("AWS_PROFILE", "AWS Profile", input_type="text", secret=False),
            ConfigField("OLLAMA_BASE_URL", "Ollama 地址", input_type="url", secret=False, placeholder="http://localhost:11434/v1"),
        ),
    ),
    ConfigGroup(
        "runtime",
        "模型与分析参数",
        "这些设置会在新启动的分析进程中生效。",
        (
            ConfigField("TRADINGAGENTS_LLM_PROVIDER", "模型供应商", input_type="select", secret=False, options=LLM_PROVIDER_OPTIONS),
            ConfigField("TRADINGAGENTS_QUICK_THINK_LLM", "快速模型 ID", input_type="text", secret=False, placeholder="gpt-5.6-luna"),
            ConfigField("TRADINGAGENTS_DEEP_THINK_LLM", "深度模型 ID", input_type="text", secret=False, placeholder="gpt-5.6"),
            ConfigField("TRADINGAGENTS_LLM_BACKEND_URL", "自定义模型接口地址", input_type="url", secret=False, placeholder="http://localhost:8000/v1"),
            ConfigField("TRADINGAGENTS_OUTPUT_LANGUAGE", "报告语言", input_type="select", secret=False, options=(("English", "English"), ("Chinese", "中文"), ("Japanese", "日本語"))),
            ConfigField("TRADINGAGENTS_TEMPERATURE", "Temperature", input_type="number", secret=False, minimum=0, maximum=2, step=0.1),
            ConfigField("TRADINGAGENTS_LLM_MAX_RETRIES", "模型重试次数", input_type="number", secret=False, minimum=0, maximum=20, step=1),
            ConfigField("TRADINGAGENTS_MAX_TOKENS", "最大输出 Token", input_type="number", secret=False, minimum=1, maximum=1_000_000, step=1),
            ConfigField("TRADINGAGENTS_OPENAI_REASONING_EFFORT", "OpenAI 推理强度", input_type="select", secret=False, options=(("minimal", "minimal"), ("low", "low"), ("medium", "medium"), ("high", "high"), ("xhigh", "xhigh"))),
            ConfigField("TRADINGAGENTS_GOOGLE_THINKING_LEVEL", "Gemini 思考等级", input_type="select", secret=False, options=(("minimal", "minimal"), ("low", "low"), ("medium", "medium"), ("high", "high"))),
            ConfigField("TRADINGAGENTS_ANTHROPIC_EFFORT", "Anthropic 推理强度", input_type="select", secret=False, options=(("low", "low"), ("medium", "medium"), ("high", "high"))),
            ConfigField("TRADINGAGENTS_CRYPTO_NEWS_LOOKBACK_DAYS", "加密新闻回看天数", input_type="number", secret=False, minimum=1, maximum=365, step=1),
            ConfigField("TRADINGAGENTS_CRYPTO_NEWS_ARTICLE_LIMIT", "加密新闻条目上限", input_type="number", secret=False, minimum=1, maximum=500, step=1),
            ConfigField("TRADINGAGENTS_CRYPTO_NEWS_TIMEOUT", "单个新闻请求超时（秒）", input_type="number", secret=False, minimum=1, maximum=120, step=0.5),
            ConfigField("TRADINGAGENTS_CRYPTO_FUNDAMENTALS_TIMEOUT", "基本面请求超时（秒）", input_type="number", secret=False, minimum=1, maximum=120, step=0.5),
            ConfigField("TRADINGAGENTS_CRYPTO_DEVELOPMENT_LOOKBACK_DAYS", "项目发布回看天数", input_type="number", secret=False, minimum=1, maximum=3650, step=1),
        ),
    ),
)

FIELD_BY_NAME = {
    field.name: field
    for group in CONFIG_GROUPS
    for field in group.fields
}

_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_MAX_VALUE_LENGTH = 8192


def _quote_env_value(value: str) -> str:
    """把值编码为 python-dotenv 可安全解析的双引号形式。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _validate_value(field: ConfigField, value: object) -> str:
    """校验单个字段并返回适合写入环境文件的字符串。"""
    if not isinstance(value, str):
        raise ConfigValidationError(f"{field.label} 必须是字符串。")
    value = value.strip()
    if not value:
        raise ConfigValidationError(f"{field.label} 不能为空；如需清除请使用清除按钮。")
    if len(value) > _MAX_VALUE_LENGTH or any(char in value for char in ("\r", "\n", "\0")):
        raise ConfigValidationError(f"{field.label} 的格式无效。")

    if field.input_type == "url":
        parsed = urlparse(value)
        invalid_url = (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(char.isspace() for char in value)
        )
        if invalid_url:
            raise ConfigValidationError(f"{field.label} 必须是完整的 HTTP(S) 地址。")
    elif field.input_type == "select":
        allowed = {option_value for option_value, _ in field.options}
        if value not in allowed:
            raise ConfigValidationError(f"{field.label} 包含不支持的选项。")
    elif field.input_type == "number":
        try:
            number = float(value)
        except ValueError as exc:
            raise ConfigValidationError(f"{field.label} 必须是数字。") from exc
        if field.minimum is not None and number < field.minimum:
            raise ConfigValidationError(f"{field.label} 不能小于 {field.minimum:g}。")
        if field.maximum is not None and number > field.maximum:
            raise ConfigValidationError(f"{field.label} 不能大于 {field.maximum:g}。")
        if field.step == 1 and not number.is_integer():
            raise ConfigValidationError(f"{field.label} 必须是整数。")
    return value


class ConfigStore:
    """以原子方式读取和更新项目的 ``.env`` 文件。"""

    def __init__(self, env_path: str | Path | None = None):
        if env_path is None:
            discovered = find_dotenv(usecwd=True)
            env_path = discovered or Path.cwd() / ".env"
        self.env_path = Path(env_path).resolve()
        self._lock = threading.RLock()

    def _file_values(self) -> dict[str, str]:
        if not self.env_path.exists():
            return {}
        return {
            key: value
            for key, value in dotenv_values(self.env_path).items()
            if isinstance(value, str)
        }

    def get_value(self, name: str) -> str:
        """返回当前进程环境优先的有效值。"""
        if name not in FIELD_BY_NAME:
            raise ConfigValidationError(f"不支持的配置项：{name}")
        return os.environ.get(name, self._file_values().get(name, ""))

    def public_snapshot(self) -> dict:
        """生成不包含任何完整密钥的前端配置模型。"""
        file_values = self._file_values()
        groups = []
        configured_count = 0
        total_secret_count = 0
        for group in CONFIG_GROUPS:
            fields = []
            for field in group.fields:
                value = os.environ.get(field.name, file_values.get(field.name, ""))
                configured = bool(value)
                if field.secret:
                    total_secret_count += 1
                    configured_count += int(configured)
                item = {
                    "name": field.name,
                    "label": field.label,
                    "inputType": field.input_type,
                    "secret": field.secret,
                    "description": field.description,
                    "placeholder": field.placeholder,
                    "configured": configured,
                    "value": "" if field.secret else value,
                }
                if field.options:
                    item["options"] = [
                        {"value": option_value, "label": option_label}
                        for option_value, option_label in field.options
                    ]
                if field.minimum is not None:
                    item["min"] = field.minimum
                if field.maximum is not None:
                    item["max"] = field.maximum
                if field.step is not None:
                    item["step"] = field.step
                fields.append(item)
            groups.append(
                {
                    "id": group.group_id,
                    "title": group.title,
                    "description": group.description,
                    "fields": fields,
                }
            )
        return {
            "envPath": str(self.env_path),
            "configuredSecrets": configured_count,
            "totalSecrets": total_secret_count,
            "groups": groups,
        }

    def apply_changes(
        self,
        updates: dict[str, object] | None = None,
        deletes: list[str] | tuple[str, ...] | None = None,
    ) -> dict:
        """校验变更、原子写入文件并同步当前进程环境。"""
        updates = updates or {}
        deletes = deletes or []
        if not isinstance(updates, dict) or not isinstance(deletes, (list, tuple)):
            raise ConfigValidationError("配置变更格式无效。")

        normalized: dict[str, str] = {}
        for name, value in updates.items():
            field = FIELD_BY_NAME.get(name)
            if field is None:
                raise ConfigValidationError(f"不支持的配置项：{name}")
            normalized[name] = _validate_value(field, value)

        delete_names: list[str] = []
        for name in deletes:
            if not isinstance(name, str) or name not in FIELD_BY_NAME:
                raise ConfigValidationError(f"不支持的配置项：{name}")
            if name in normalized:
                raise ConfigValidationError(f"配置项 {name} 不能同时更新和清除。")
            if name not in delete_names:
                delete_names.append(name)

        if not normalized and not delete_names:
            return self.public_snapshot()

        with self._lock:
            self._write_file(normalized, set(delete_names))
            for name, value in normalized.items():
                os.environ[name] = value
            for name in delete_names:
                os.environ.pop(name, None)
        return self.public_snapshot()

    def _write_file(self, updates: dict[str, str], deletes: set[str]) -> None:
        """保留无关行与注释，并通过同目录临时文件完成原子替换。"""
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        original = self.env_path.read_text(encoding="utf-8") if self.env_path.exists() else ""
        output: list[str] = []
        written: set[str] = set()
        for line in original.splitlines():
            match = _ENV_LINE_RE.match(line)
            if not match:
                output.append(line)
                continue
            name = match.group(1)
            if name in deletes:
                continue
            if name in updates:
                if name not in written:
                    output.append(f"{name}={_quote_env_value(updates[name])}")
                    written.add(name)
                continue
            output.append(line)

        missing = [name for name in updates if name not in written]
        if missing:
            if output and output[-1].strip():
                output.append("")
            output.extend(f"{name}={_quote_env_value(updates[name])}" for name in missing)

        content = "\n".join(output)
        if content:
            content += "\n"
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.env_path.name}.",
            suffix=".tmp",
            dir=self.env_path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            with suppress(OSError):
                temporary_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temporary_path, self.env_path)
        finally:
            temporary_path.unlink(missing_ok=True)
