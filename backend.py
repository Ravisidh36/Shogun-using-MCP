import os 
import certifi
from dotenv import load_dotenv

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from typing import TypedDict, Annotated
import operator
import uuid
import asyncio
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langchain_core.messages import (
    AnyMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
)
from tools.flight_tool import fetch_flight_report
from mcp_client import tavily_mcp_search, extract_destination, forecast_mcp_search, weather_mcp_search


def get_database_url():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "DATABASE_URL is missing. Please add your Render PostgreSQL External Database URL to .env"
        )

    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"

    return database_url

from langchain_groq import ChatGroq

GROQ_API_KEY = os.getenv("GROQ_API_KEY") 

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing. Set GROQ_API_KEY  in your .env file.")

llm = ChatGroq(
    model="openai/gpt-oss-20b",
    temperature=0,
    api_key=os.getenv("GROQ_API_KEY"),
)



# =========================
# State
# =========================

class TravelState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str
    flight_results: str
    flight_data: dict | None
    hotel_results: str
    itinerary: str
    llm_calls: int
    weather_results: str
    weather_data: dict | None


# =========================
# Flight Agent
# =========================

# def flight_agent(state: TravelState):
#     query = state["user_query"]
#     flight_data = search_flights(query)

#     return {
#         "flight_results": flight_data,
#         "messages": [
#             AIMessage(content="Flight results fetched.")
#         ],
#         "llm_calls": state.get("llm_calls", 0) + 1
#     }




# Flight Agent
async def flight_agent(state: TravelState):
    print("\nINSIDE FLIGHT AGENT\n")

    query = state["user_query"]

    try:
        # Direct AviationStack REST call — one request, no
        # subprocess/MCP/uvx dependency. See tools/flight_tool.py.
        # Runs in a thread since fetch_flight_report uses the
        # blocking `requests` library and this is an async node.
        report = await asyncio.to_thread(fetch_flight_report, query)

    except Exception as e:
        print("FLIGHT AGENT ERROR:", repr(e))
        return {
            "flight_results": (
                "Live flight status is temporarily unavailable. "
                "We'll rely on general route guidance for this trip instead."
            ),
            "flight_data": None,
            "messages": [
                AIMessage(content="Flight lookup unavailable, continuing with general guidance.")
            ],
            "llm_calls": state.get("llm_calls", 0) + 1
        }

    if report.get("error"):
        print("FLIGHT API ERROR:", report["error"])

    # flight_data is the structured payload the frontend renders as
    # cards. flight_results stays a short text summary used both as
    # a graceful text fallback and as context for later LLM prompts
    # (itinerary_agent / final_agent).
    flight_data = {
        "route_info": report.get("route_info") or "",
        "flights": report.get("flights") or [],
        "notice": report.get("notice"),
        "unavailable": bool(report.get("error")) and not report.get("flights"),
    }

    if flight_data["flights"]:
        flight_results = report["summary_text"]
    elif report.get("error"):
        flight_results = (
            "Live flight status is temporarily unavailable for this route. "
            "We'll rely on general route guidance for this trip instead."
        )
    else:
        flight_results = report["summary_text"]  # "no live flights in range" + price notice

    return {
        "flight_results": flight_results,
        "flight_data": flight_data,
        "messages": [
            AIMessage(
                content="Flight information fetched"
            )
        ],
        "llm_calls": state.get("llm_calls", 0) + 1
    }





# =========================
# Hotel Agent
# =========================

def _to_text(value) -> str:
    """
    MCP tool calls (langchain_mcp_adapters) can return a list of
    content blocks instead of a plain string. Every agent result we
    hand to the frontend must be a string (it's fed to marked.parse
    client-side), so normalize here rather than downstream.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(_item_to_text(item) for item in value)
    return _item_to_text(value)


def _item_to_text(item) -> str:
    if isinstance(item, str):
        return _maybe_unwrap_json_blob(item)
    if isinstance(item, dict):
        if isinstance(item.get("text"), str):
            return _maybe_unwrap_json_blob(item["text"])

        # Tavily-style search result shape -- render as clean
        # markdown instead of a raw Python dict repr.
        title = item.get("title") or item.get("name")
        url = item.get("url") or item.get("link")
        body = item.get("content") or item.get("snippet") or item.get("description")

        if title or body or url:
            block = []
            if title:
                block.append(f"**{title}**")
            if body:
                block.append(str(body).strip())
            if url:
                block.append(f"[{url}]({url})")
            return "\n".join(block)

        import json
        return json.dumps(item, indent=2, ensure_ascii=False, default=str)
    return str(item)


def _maybe_unwrap_json_blob(text: str) -> str:
    # Some MCP servers embed a JSON-encoded payload *as* the text
    # content instead of returning it structured. If so, reformat
    # that too rather than showing the raw JSON string.
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return text

    import json
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        return text

    results = parsed.get("results") if isinstance(parsed, dict) else parsed
    if isinstance(results, list) and results and all(isinstance(r, dict) for r in results):
        return "\n\n".join(_item_to_text(r) for r in results)

    return text


# In-process cache: same normalized query within this server's
# lifetime reuses the last Tavily result instead of re-hitting an
# already-limited API. Cleared on restart/redeploy — that's fine,
# it only exists to stop the *same* destination search from being
# re-fired repeatedly during a demo/testing session.
_hotel_cache: dict[str, str] = {}


def _is_rate_limited(error: Exception) -> bool:
    text = str(error).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


async def hotel_agent(state: TravelState):
    query = f"Best hotels for {state['user_query']}"
    cache_key = query.strip().lower()

    if cache_key in _hotel_cache:
        hotel_results = _hotel_cache[cache_key]

        return {
            "hotel_results": hotel_results,
            "messages": [
                AIMessage(content="Hotel information fetched (cached).")
            ],
            "llm_calls": state.get("llm_calls", 0) + 1
        }

    try:
        hotel_results = _to_text(await tavily_mcp_search(query))
        _hotel_cache[cache_key] = hotel_results

    except Exception as e:
        print("HOTEL AGENT ERROR:", repr(e))

        if _is_rate_limited(e):
            hotel_results = (
                "Lodging search is temporarily unavailable. We've prepared "
                "recommendations based on your destination and budget — ask "
                "the itinerary for specific neighborhood or hotel-type guidance."
            )
        else:
            hotel_results = (
                "Lodging search is temporarily unavailable right now. "
                "We'll continue building the rest of your trip."
            )

    return {
        "hotel_results": hotel_results,
        "messages": [
            AIMessage(content="Hotel information fetched.")
        ],
        "llm_calls": state.get("llm_calls", 0) + 1
    }




# =========================
# Weather Agent
# =========================

def _unwrap_mcp_dict(value):
    """
    FastMCP tools that return a plain Python dict still get
    serialized over the wire as an MCP content block, so what comes
    back through langchain_mcp_adapters is often
    [{"type": "text", "text": "<json string>"}] rather than the
    dict itself. The old `isinstance(value, dict)` check here never
    matched that shape, which is exactly why raw MCP envelopes were
    showing up in the Weather tab. This unwraps either shape and
    returns a real dict, or None if it genuinely can't be parsed.
    """
    if isinstance(value, dict):
        return value

    if isinstance(value, list):
        for item in value:
            text = item.get("text") if isinstance(item, dict) else None
            if isinstance(text, str):
                import json
                try:
                    parsed = json.loads(text)
                except (ValueError, TypeError):
                    continue
                if isinstance(parsed, dict):
                    return parsed

    return None


def _format_forecast_datetime(raw) -> str:
    from datetime import datetime
    if not raw:
        return "Unknown time"
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%a %d %b, %H:%M")
    except (ValueError, TypeError):
        return raw


async def weather_agent(state: TravelState):

    city = await extract_destination(state["user_query"])

    try:
        raw_weather = await weather_mcp_search(city)
        raw_forecast = await forecast_mcp_search(city)
    except Exception as e:
        print("WEATHER AGENT ERROR:", repr(e))
        return {
            "weather_results": f"Weather information is temporarily unavailable for {city}.",
            "weather_data": None,
            "messages": [
                AIMessage(content="Weather lookup unavailable.")
            ]
        }

    weather_dict = _unwrap_mcp_dict(raw_weather)
    forecast_dict = _unwrap_mcp_dict(raw_forecast)

    weather_ok = bool(weather_dict) and "temperature_c" in weather_dict
    forecast_entries = (forecast_dict or {}).get("forecast") or []

    lines = [f"### Current Weather — {city}"]

    if weather_ok:
        lines.append(
            f"- **Temperature:** {weather_dict.get('temperature_c')}°C "
            f"(feels like {weather_dict.get('feels_like_c')}°C)\n"
            f"- **Condition:** {weather_dict.get('condition', 'N/A')}\n"
            f"- **Humidity:** {weather_dict.get('humidity', 'N/A')}%\n"
            f"- **Wind speed:** {weather_dict.get('wind_speed', 'N/A')} m/s"
        )
    else:
        lines.append(f"Weather data is temporarily unavailable for {city}.")

    lines.append("\n### Forecast")

    if forecast_entries:
        for entry in forecast_entries:
            lines.append(
                f"- **{_format_forecast_datetime(entry.get('datetime'))}:** "
                f"{entry.get('temperature', 'N/A')}°C, {entry.get('weather', 'N/A')}"
            )
    else:
        lines.append("Forecast data is temporarily unavailable.")

    # Structured payload for the frontend's weather cards. None only
    # when we genuinely have nothing usable, so the UI can show a
    # clean fallback instead of an empty/broken layout.
    weather_data = None
    if weather_ok or forecast_entries:
        weather_data = {
            "city": (weather_dict or {}).get("city") or city,
            "temperature_c": (weather_dict or {}).get("temperature_c"),
            "feels_like_c": (weather_dict or {}).get("feels_like_c"),
            "humidity": (weather_dict or {}).get("humidity"),
            "condition": (weather_dict or {}).get("condition"),
            "wind_speed": (weather_dict or {}).get("wind_speed"),
            "forecast": [
                {
                    "datetime": entry.get("datetime"),
                    "datetime_label": _format_forecast_datetime(entry.get("datetime")),
                    "temperature": entry.get("temperature"),
                    "weather": entry.get("weather"),
                }
                for entry in forecast_entries
            ],
        }

    return {
        "weather_results": "\n".join(lines),
        "weather_data": weather_data,
        "messages": [
            AIMessage(
                content="Weather information fetched"
            )
        ]
    }




# =========================
# Itinerary Agent
# =========================

async def itinerary_agent(state: TravelState):
    prompt = f"""
Create a complete travel itinerary.

User Query:
{state['user_query']}

Flight Results:
{state['flight_results']}

Hotel Results:
{state['hotel_results']}

Weather Results:
{state['weather_results']}

Make the itinerary practical, budget-aware, and easy to follow.
"""

    response = await llm.ainvoke([
        SystemMessage(content="You are an expert travel planner."),
        HumanMessage(content=prompt)
    ])

    return {
        "itinerary": response.content,
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1
    }



# =========================
# Final Response Agent
# =========================

async def final_agent(state: TravelState):
    final_prompt = f"""
Generate the final travel response for the user.

User Request:
{state['user_query']}

Flights:
{state['flight_results']}

Hotels:
{state['hotel_results']}

Weather:
{state['weather_results']}

Itinerary:
{state['itinerary']}

Format the final answer beautifully using these sections:

1. Trip Summary
2. Flight Information
3. Hotel Suggestions
4. Weather Information
5. Day-by-Day Itinerary
6. Estimated Budget
7. Final Recommendations


Important:
- Be clear and practical.
- Mention that live flight API may not provide ticket prices if pricing is unavailable.
- Include weather-based travel advice.
- Keep the response useful for real travel planning.
"""

    response = await llm.ainvoke([
        SystemMessage(content="You are a professional AI travel booking assistant."),
        HumanMessage(content=final_prompt)
    ])

    return {
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1
    }


# =========================
# Build Graph
# =========================

graph = StateGraph(TravelState)

graph.add_node("flight_agent", flight_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("weather_agent", weather_agent)
graph.add_node("itinerary_agent", itinerary_agent)
graph.add_node("final_agent", final_agent)

graph.add_edge(START, "flight_agent")
graph.add_edge("flight_agent", "hotel_agent")
graph.add_edge("hotel_agent", "weather_agent")
graph.add_edge("weather_agent", "itinerary_agent")
graph.add_edge("itinerary_agent", "final_agent")
graph.add_edge("final_agent", END)


# =========================
# PostgreSQL Checkpointer
# =========================
DATABASE_URL = get_database_url()

_pool: AsyncConnectionPool | None = None
_travel_graph = None
_graph_init_lock = asyncio.Lock()


async def get_travel_graph():
    """
    Lazily creates the async connection pool + checkpointer on first
    use, inside a running event loop. Using AsyncConnectionPool (not a
    single bare connection) means dropped/idle Render connections are
    detected and replaced automatically instead of causing a 500 on
    the first request after the connection goes stale.
    """
    global _pool, _travel_graph

    if _travel_graph is not None:
        return _travel_graph

    async with _graph_init_lock:
        if _travel_graph is not None:
            return _travel_graph

        _pool = AsyncConnectionPool(
            conninfo=DATABASE_URL,
            max_size=5,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=False,
        )
        await _pool.open()

        checkpointer = AsyncPostgresSaver(_pool)
        await checkpointer.setup()

        _travel_graph = graph.compile(checkpointer=checkpointer)

    return _travel_graph



# =========================
# Function for FastAPI
# =========================

async def run_travel_agent(user_input: str, thread_id: str | None = None):
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    travel_graph = await get_travel_graph()

    result = await travel_graph.ainvoke(
        {
            "messages": [
                HumanMessage(content=user_input)
            ],
            "user_query": user_input,
            "flight_results": "",
            "flight_data": None,
            "hotel_results": "",
            "weather_results": "",
            "weather_data": None,
            "itinerary": "",
            "llm_calls": 0
        },
        config=config
    )

    final_answer = result["messages"][-1].content

    return {
        "thread_id": thread_id,
        "answer": final_answer,
        "flight_results": result.get("flight_results", ""),
        "flight_data": result.get("flight_data"),
        "hotel_results": result.get("hotel_results", ""),
        "weather_results": result.get("weather_results", ""),
        "weather_data": result.get("weather_data"),
        "itinerary": result.get("itinerary", ""),
        "llm_calls": result.get("llm_calls", 0),
    }