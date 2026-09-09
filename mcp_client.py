import os
import sys
from pathlib import Path

import certifi
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_groq import ChatGroq


# ==========================================
# Environment configuration
# ==========================================

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

load_dotenv()


# ==========================================
# API Keys
# ==========================================

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
AVIATION_STACK_API_KEY = os.getenv("AVIATIONSTACK_API_KEY")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY") 

# ==========================================
# Project paths
# ==========================================

PROJECT_DIR = Path(__file__).resolve().parent

WEATHER_SERVER_PATH = PROJECT_DIR / "mcp_customserver.py"


# ==========================================
# Validate important configuration
# ==========================================

if not TAVILY_API_KEY:
    print("WARNING: TAVILY_API_KEY is not set.")

if not AVIATION_STACK_API_KEY:
    print("WARNING: AVIATIONSTACK_API_KEY is not set.")

if not OPENWEATHER_API_KEY:
    print("WARNING: OPENWEATHER_API_KEY is not set.")

if not GROQ_API_KEY:
    print("WARNING: GROQ_API_KEY is not set.")


# ==========================================
# Environment for local MCP servers
# ==========================================

AVIATION_ENV = os.environ.copy()

AVIATION_ENV["AVIATION_STACK_API_KEY"] = (
    AVIATION_STACK_API_KEY or ""
)


WEATHER_ENV = os.environ.copy()

WEATHER_ENV["OPENWEATHER_API_KEY"] = (
    OPENWEATHER_API_KEY or ""
)


# ==========================================
# GROQ LLM
# ==========================================



llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0,
    api_key=os.getenv("GROQ_API_KEY")
)


# ==========================================
# MCP Client
# ==========================================

client = MultiServerMCPClient(
    {
        # ----------------------------------
        # Tavily MCP
        # ----------------------------------

        "tavily": {
            "transport": "streamable_http",

            "url": (
                "https://mcp.tavily.com/mcp/"
                f"?tavilyApiKey={TAVILY_API_KEY}"
            )
        },


        # ----------------------------------
        # AviationStack MCP
        # ----------------------------------

        "aviationstack": {
            "transport": "stdio",

            "command": "uvx",
            "args": [
                "--with", "mcp<2",
                "aviationstack-mcp"
            ],

            "env": AVIATION_ENV
        },


        # ----------------------------------
        # Weather MCP
        # ----------------------------------

        "weather": {
            "transport": "stdio",

            # Use the same Python environment
            # running this application.
            "command": sys.executable,

            "args": [
                str(WEATHER_SERVER_PATH)
            ],

            "env": WEATHER_ENV
        }
    }
)


# ==========================================
# Diagnostic: Get tools from all MCP servers
# ==========================================

async def get_all_tools():

    all_tools = []

    servers = (
        "tavily",
        "aviationstack",
        "weather"
    )

    for server_name in servers:

        try:

            tools = await client.get_tools(
                server_name=server_name
            )

            all_tools.extend(tools)

            print(
                f"\n{'=' * 50}"
            )

            print(
                f"{server_name.upper()} MCP"
            )

            print(
                f"{'=' * 50}"
            )

            if not tools:
                print("No tools found.")

            else:

                for tool in tools:

                    print(
                        f"- {tool.name}"
                    )

        except Exception as error:

            print(
                f"\n{'=' * 50}"
            )

            print(
                f"{server_name.upper()} MCP ERROR"
            )

            print(
                f"{'=' * 50}"
            )

            print(
                repr(error)
            )

    return all_tools


# ==========================================
# Tavily MCP
# ==========================================

search_tool = None


async def initialize_mcp():

    global search_tool

    if search_tool is not None:
        return

    tools = await client.get_tools(
        server_name="tavily"
    )

    tools_by_name = {
        tool.name: tool
        for tool in tools
    }

    search_tool = tools_by_name.get(
        "tavily_search"
    )

    if search_tool is None:

        available_tools = ", ".join(
            tools_by_name.keys()
        )

        raise RuntimeError(
            "Tavily MCP connected, but "
            "'tavily_search' was not found.\n"
            f"Available tools: "
            f"{available_tools or 'none'}"
        )


async def tavily_mcp_search(query: str):

    await initialize_mcp()

    result = await search_tool.ainvoke(
        {
            "query": query
        }
    )

    return result


# ==========================================
# AviationStack MCP
# ==========================================

aviation_tools = {}


async def initialize_aviation_tools():

    global aviation_tools

    if aviation_tools:
        return

    tools = await client.get_tools(
        server_name="aviationstack"
    )

    aviation_tools = {
        tool.name: tool
        for tool in tools
    }

    if not aviation_tools:

        raise RuntimeError(
            "AviationStack MCP connected "
            "but returned no tools."
        )


async def aviation_mcp_call(
    tool_name: str,
    tool_args: dict | None = None
):

    await initialize_aviation_tools()

    tool = aviation_tools.get(
        tool_name
    )

    if tool is None:

        available_tools = ", ".join(
            sorted(
                aviation_tools.keys()
            )
        )

        raise ValueError(
            f"AviationStack tool "
            f"'{tool_name}' was not found.\n"
            f"Available tools: "
            f"{available_tools or 'none'}"
        )

    result = await tool.ainvoke(
        tool_args or {}
    )

    return result


# ==========================================
# Weather MCP
# ==========================================

weather_tool = None
forecast_tool = None


async def initialize_weather_tools():

    global weather_tool
    global forecast_tool

    if (
        weather_tool is not None
        and forecast_tool is not None
    ):
        return

    if not WEATHER_SERVER_PATH.exists():

        raise FileNotFoundError(
            "Weather MCP server was not found:\n"
            f"{WEATHER_SERVER_PATH}"
        )

    tools = await client.get_tools(
        server_name="weather"
    )

    tools_by_name = {
        tool.name: tool
        for tool in tools
    }

    weather_tool = tools_by_name.get(
        "get_current_weather"
    )

    forecast_tool = tools_by_name.get(
        "get_forecast"
    )

    missing_tools = []

    if weather_tool is None:
        missing_tools.append(
            "get_current_weather"
        )

    if forecast_tool is None:
        missing_tools.append(
            "get_forecast"
        )

    if missing_tools:

        available_tools = ", ".join(
            tools_by_name.keys()
        )

        raise RuntimeError(
            "Missing Weather MCP tools:\n"
            f"{', '.join(missing_tools)}\n"
            f"Available tools: "
            f"{available_tools or 'none'}"
        )


async def weather_mcp_search(city: str):

    await initialize_weather_tools()

    result = await weather_tool.ainvoke(
        {
            "city": city
        }
    )

    return result


async def forecast_mcp_search(city: str):

    await initialize_weather_tools()

    result = await forecast_tool.ainvoke(
        {
            "city": city
        }
    )

    return result


# ==========================================
# Destination extractor
# ==========================================

async def extract_destination(query: str):

    prompt = f"""
Extract only the destination city or country
from the following travel query.

Query:
{query}

Return ONLY the destination name.
Do not explain anything.
"""

    response = await llm.ainvoke(prompt)

    return response.content.strip()