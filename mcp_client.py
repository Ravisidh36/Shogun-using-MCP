import os
import shutil
import sys
from pathlib import Path
from typing import Any

import certifi
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_mcp_adapters.client import MultiServerMCPClient


# =========================================================
# Environment setup
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# Support both environment-variable names.
AVIATION_STACK_API_KEY = (
    os.getenv("AVIATION_STACK_API_KEY")
    or os.getenv("AVIATIONSTACK_API_KEY")
)

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

WEATHER_SERVER_PATH = BASE_DIR / "custom_weather_mcp_server.py"
UVX_COMMAND = shutil.which("uvx") or "uvx"


def _require_env(name: str, value: str | None) -> str:
    """Return an environment value or raise a readable setup error."""

    if not value:
        raise RuntimeError(
            f"{name} is missing. "
            f"Add {name}=your_key to the project .env file."
        )

    return value


def _subprocess_env(**updates: str | None) -> dict[str, str]:
    """
    Preserve the current Windows/Conda environment and add MCP API keys.
    """

    env = os.environ.copy()

    for key, value in updates.items():
        if value:
            env[key] = value

    return env


# =========================================================
# LLM
# =========================================================

# Groq's current on-demand GPT-OSS-20B limit is 8K TPM.  The application
# can assemble large MCP/search contexts, so protect every ChatGroq instance
# from accidentally requesting an oversized context.  This is deliberately
# installed at the class level because backend.py creates its own ChatGroq
# instance independently of this module.
_ORIGINAL_CHATGROQ_INVOKE = ChatGroq.invoke
_SAFE_CONTEXT_CHARS = 20000
_SAFE_MAX_OUTPUT_TOKENS = 1200


def _clip_llm_content(content: Any, limit: int = _SAFE_CONTEXT_CHARS) -> Any:
    """Keep large retrieved contexts bounded while preserving both ends."""
    if not isinstance(content, str) or len(content) <= limit:
        return content

    head = int(limit * 0.72)
    tail = limit - head
    return (
        content[:head]
        + "\n\n[Context trimmed to stay within the model's request budget. "
        "Prefer the most relevant information above.]\n\n"
        + content[-tail:]
    )


def _safe_chatgroq_invoke(self, input, config=None, *, stop=None, **kwargs):
    """Bound prompt size and completion budget before calling Groq."""
    try:
        if isinstance(input, list):
            trimmed_input = []
            for message in input:
                if hasattr(message, "content"):
                    try:
                        message = message.model_copy(deep=True)
                        message.content = _clip_llm_content(message.content)
                    except Exception:
                        # Fall back to the original message if a custom message
                        # type cannot be copied.
                        pass
                trimmed_input.append(message)
            input = trimmed_input
        elif isinstance(input, str):
            input = _clip_llm_content(input)

        # Do not override an explicit caller value, but cap the default output
        # budget so input + output stays comfortably below the 8K TPM ceiling.
        if "max_tokens" not in kwargs:
            kwargs["max_tokens"] = _SAFE_MAX_OUTPUT_TOKENS

        return _ORIGINAL_CHATGROQ_INVOKE(
            self,
            input,
            config=config,
            stop=stop,
            **kwargs,
        )
    except TypeError:
        # Older LangChain versions may reject max_tokens through invoke kwargs.
        kwargs.pop("max_tokens", None)
        return _ORIGINAL_CHATGROQ_INVOKE(
            self,
            input,
            config=config,
            stop=stop,
            **kwargs,
        )


# Install once; re-imports of mcp_client must not wrap the method repeatedly.
if not getattr(ChatGroq, "_shogun_safe_invoke", False):
    ChatGroq.invoke = _safe_chatgroq_invoke
    ChatGroq._shogun_safe_invoke = True


llm = ChatGroq(
    model="openai/gpt-oss-20b",
    temperature=0,
    api_key=os.getenv("GROQ_API_KEY"),
    max_tokens=_SAFE_MAX_OUTPUT_TOKENS,
)


# =========================================================
# MCP client
# =========================================================

client = MultiServerMCPClient(
    {
        "tavily": {
            "transport": "streamable_http",
            "url": (
                "https://mcp.tavily.com/mcp/"
                f"?tavilyApiKey={TAVILY_API_KEY or ''}"
            ),
        },

        "aviationstack": {
            "transport": "stdio",
            "command": UVX_COMMAND,
            "args": [
                "aviationstack-mcp",
            ],
            "env": _subprocess_env(
                AVIATION_STACK_API_KEY=AVIATION_STACK_API_KEY,
            ),
        },

        "weather": {
            "transport": "stdio",

            # Uses the Python executable from the active Conda environment.
            "command": sys.executable,

            # Uses the weather server inside the current project folder.
            "args": [
                str(WEATHER_SERVER_PATH),
            ],

            "env": _subprocess_env(
                OPENWEATHER_API_KEY=OPENWEATHER_API_KEY,
            ),
        },
    }
)


async def _get_server_tool(
    server_name: str,
    tool_name: str,
):
    """
    Load one tool from one MCP server.

    This prevents a broken weather or AviationStack server from
    crashing an unrelated Tavily request.
    """

    if server_name == "tavily":
        _require_env(
            "TAVILY_API_KEY",
            TAVILY_API_KEY,
        )

    elif server_name == "aviationstack":
        _require_env(
            "AVIATION_STACK_API_KEY",
            AVIATION_STACK_API_KEY,
        )

        if shutil.which("uvx") is None:
            raise RuntimeError(
                "uvx was not found. Install uv, reopen the terminal, "
                "activate the travel environment, and run "
                "`uvx --version`."
            )

    elif server_name == "weather":
        _require_env(
            "OPENWEATHER_API_KEY",
            OPENWEATHER_API_KEY,
        )

        if not WEATHER_SERVER_PATH.is_file():
            raise FileNotFoundError(
                f"Weather MCP server not found: "
                f"{WEATHER_SERVER_PATH}"
            )

    # Important: load only the requested MCP server.
    tools = await client.get_tools(
        server_name=server_name,
    )

    tool = next(
        (
            item
            for item in tools
            if item.name == tool_name
        ),
        None,
    )

    if tool is None:
        available_tools = (
            ", ".join(
                sorted(item.name for item in tools)
            )
            or "none"
        )

        raise RuntimeError(
            f"MCP tool '{tool_name}' was not found "
            f"on server '{server_name}'. "
            f"Available tools: {available_tools}"
        )

    return tool


# =========================================================
# MCP connection test
# =========================================================

async def get_all_tools() -> None:
    """
    Test every MCP server independently.

    One failed server will not stop the remaining tests.
    """

    for server_name in (
        "tavily",
        "aviationstack",
        "weather",
    ):
        try:
            tools = await client.get_tools(
                server_name=server_name,
            )

            tool_names = (
                ", ".join(
                    tool.name
                    for tool in tools
                )
                or "no tools"
            )

            print(
                f"{server_name}: OK -> {tool_names}"
            )

        except Exception as exc:
            print(
                f"{server_name}: FAILED -> "
                f"{type(exc).__name__}: {exc}"
            )


# =========================================================
# Tavily MCP
# =========================================================

async def tavily_mcp_search(query: str):
    search_tool = await _get_server_tool(
        "tavily",
        "tavily_search",
    )

    return await search_tool.ainvoke(
        {
            "query": query,
        }
    )


# =========================================================
# AviationStack MCP
# =========================================================

async def aviation_mcp_call(
    tool_name: str,
    tool_args: dict[str, Any] | None = None,
):
    aviation_tool = await _get_server_tool(
        "aviationstack",
        tool_name,
    )

    return await aviation_tool.ainvoke(
        tool_args or {}
    )


# =========================================================
# Weather MCP
# =========================================================

async def weather_mcp_search(city: str):
    weather_tool = await _get_server_tool(
        "weather",
        "get_current_weather",
    )

    return await weather_tool.ainvoke(
        {
            "city": city,
        }
    )


async def forecast_mcp_search(city: str):
    forecast_tool = await _get_server_tool(
        "weather",
        "get_forecast",
    )

    return await forecast_tool.ainvoke(
        {
            "city": city,
        }
    )


# =========================================================
# Destination extractor
# =========================================================

def extract_destination(query: str) -> str:
    prompt = f"""
Extract only the destination city or country from the travel request.

Travel request:
{query}

Return only the destination name.
Do not add any explanation.
"""

    response = llm.invoke(prompt)

    destination = str(
        response.content
    ).strip()

    if not destination:
        raise ValueError(
            "The destination could not be extracted."
        )

    return destination
