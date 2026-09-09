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
# from tools.tavily_tool import tavily_search
# from tools.flight_tool import search_flights
from mcp_client import tavily_mcp_search, aviation_mcp_call, extract_destination, forecast_mcp_search, weather_mcp_search


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

GROQ_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("GOOGLE_API_KEY")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing. Set GEMINI_API_KEY (or GOOGLE_API_KEY) in your .env file.")

llm = ChatGroq(
    model="openai/gpt-oss-120b",
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
    hotel_results: str
    itinerary: str
    llm_calls: int
    weather_results: str


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




# Flight Tool Router Prompt
FLIGHT_AGENT_PROMPT = """
You are a travel flight expert.

User Query:
{query}

Airport Information:
{airport_data}

Airline Information:
{airline_data}

Generate:

1. Likely departure airport
2. Likely arrival airport
3. Airlines serving this route
4. Typical flight duration
5. Estimated airfare range
6. Peak season pricing warning
7. Booking advice

Return concise travel guidance.
"""


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
        # markdown instead of a raw Python dict repr, which is what
        # was showing up as "distorted" text in the Flight/Lodging
        # tabs.
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




# Flight Agent
async def flight_agent(state: TravelState):
    print("\nINSIDE FLIGHT AGENT\n")

    query = state["user_query"]

    try:

        airports = await aviation_mcp_call(
            "list_airports"
        )

        airlines = await aviation_mcp_call(
            "list_airlines"
        )


        print("\nAIRPORTS:", airports)
        print("\nAIRLINES:", airlines)

        prompt = FLIGHT_AGENT_PROMPT.format(
            query=query,
            airport_data=str(airports)[:3000],
            airline_data=str(airlines)[:3000]
        )

        response = await llm.ainvoke([
            SystemMessage(
                content="You are an expert travel flight planner."
            ),
            HumanMessage(content=prompt)
        ])

        flight_data = response.content

    except Exception as e:

        flight_data = f"Flight information unavailable: {str(e)}"

    flight_data = _to_text(flight_data)

    return {
        "flight_results": flight_data,
        "messages": [
            AIMessage(
                content="Flight recommendations generated"
            )
        ],
        "llm_calls": state.get("llm_calls", 0) + 1
    }





# =========================
# Hotel Agent
# =========================

async def hotel_agent(state: TravelState):
    query = f"Best hotels for {state['user_query']}"
    # hotel_results = tavily_search(query)
    hotel_results = _to_text(await tavily_mcp_search(query))

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

async def weather_agent(state: TravelState):

    city = await extract_destination(state["user_query"])

    weather_data = await weather_mcp_search(city)
    forecast_data = await forecast_mcp_search(city)

    lines = [f"### Current Weather \u2014 {city}"]

    if isinstance(weather_data, dict) and "temperature_c" in weather_data:
        lines.append(
            f"- **Temperature:** {weather_data.get('temperature_c')}\u00b0C "
            f"(feels like {weather_data.get('feels_like_c')}\u00b0C)\n"
            f"- **Condition:** {weather_data.get('condition', 'N/A')}\n"
            f"- **Humidity:** {weather_data.get('humidity', 'N/A')}%\n"
            f"- **Wind speed:** {weather_data.get('wind_speed', 'N/A')} m/s"
        )
    else:
        lines.append(f"Weather data unavailable: {weather_data}")

    lines.append("\n### Forecast")

    forecast_entries = (
        forecast_data.get("forecast")
        if isinstance(forecast_data, dict) else None
    )

    if forecast_entries:
        for entry in forecast_entries:
            lines.append(
                f"- **{entry.get('datetime', 'Unknown time')}:** "
                f"{entry.get('temperature', 'N/A')}\u00b0C, {entry.get('weather', 'N/A')}"
            )
    else:
        lines.append(f"Forecast data unavailable: {forecast_data}")

    return {
        "weather_results": "\n".join(lines),
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
            "hotel_results": "",
            "weather_results": "",
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
        "hotel_results": result.get("hotel_results", ""),
        "weather_results": result.get("weather_results", ""),
        "itinerary": result.get("itinerary", ""),
        "llm_calls": result.get("llm_calls", 0),
    }