import os
import certifi
from dotenv import load_dotenv

load_dotenv()
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from typing import Any, TypedDict, Annotated
import operator
import uuid
import asyncio
import json
import re
import psycopg
from psycopg.rows import dict_row
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command, interrupt
from langchain_core.messages import AnyMessage, HumanMessage, AIMessage, SystemMessage
from langchain_groq import ChatGroq

from mcp_client import (
    tavily_mcp_search,
    aviation_mcp_call,
    extract_destination,
    forecast_mcp_search,
    weather_mcp_search,
)


def get_database_url():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError(
            "DATABASE_URL is missing. "
            "Please add your Render PostgreSQL External Database URL to .env"
        )
    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"
    return database_url


GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing. Please add it to your .env file.")

llm = ChatGroq(
    model="openai/gpt-oss-20b",
    temperature=0,
    api_key=os.getenv("GROQ_API_KEY"),
)


class TravelState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str
    guardrail_allowed: bool
    guardrail_reason: str
    selected_agents: list[str]
    trip_constraints: dict[str, Any]
    supervisor_reasoning: str
    flight_results: str
    hotel_results: str
    weather_results: str
    itinerary: str
    budget_results: str
    approval_request: str
    approved: bool
    human_feedback: str
    final_response: str
    llm_calls: int


KNOWN_AGENTS = {
    "flight_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "itinerary_agent",
}

AGENT_ORDER = [
    "flight_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "itinerary_agent",
]

# Deterministic checks are intentionally used for high-impact routing decisions.
# The LLM must not silently turn a country into a city.
US_CITY_HINTS = {
    "new york", "nyc", "jfk", "newark", "ewr", "los angeles", "lax",
    "san francisco", "sfo", "chicago", "ord", "atlanta", "atl",
    "washington", "washington dc", "dulles", "iad", "boston", "bos",
    "seattle", "sea", "miami", "mia", "dallas", "dfw", "houston", "iah",
    "denver", "den", "phoenix", "phx", "las vegas", "orlando", "mco",
    "philadelphia", "phl", "detroit", "dtw", "minneapolis", "msp",
    "charlotte", "clt", "nashville", "bna", "san diego", "san jose",
    "portland", "pdx", "salt lake city", "slc",
}


def _llm_text(system_prompt: str, user_prompt: str) -> str:
    response = llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    )
    return str(response.content)


def _json_from_llm(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("The model did not return a JSON object.")
    return json.loads(text[start : end + 1])


def _empty_constraints() -> dict[str, Any]:
    return {
        "destination": "",
        "origin": "",
        "duration": "",
        "budget": "",
        "travel_style": "",
        "special_preferences": [],
    }


def _normalized_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.lower()).strip()


def _is_flight_request(query: str) -> bool:
    q = _normalized_query(query)
    return any(
        phrase in q
        for phrase in (
            "flight", "flights", "airfare", "airline", "airlines",
            "fly from", "fly to", "flying from", "flying to",
        )
    )


def _needs_destination_clarification(query: str) -> bool:
    """Detect a flight request whose U.S. destination is only a country."""
    if not _is_flight_request(query):
        return False

    q = _normalized_query(query)
    us_mentions = (
        "usa", "u.s.a.", "united states", "united states of america", "us", "america"
    )
    if not any(
        re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", q)
        for term in us_mentions
    ):
        return False

    return not any(city in q for city in US_CITY_HINTS)


def _fallback_agents(query: str) -> list[str]:
    """Safe, narrow routing fallback if supervisor JSON parsing fails."""
    q = _normalized_query(query)
    if _needs_destination_clarification(query) or _is_flight_request(query):
        return ["flight_agent"]
    if any(x in q for x in ("hotel", "hostel", "resort", "accommodation", "stay")):
        return ["hotel_agent"]
    if any(x in q for x in ("weather", "forecast", "climate", "temperature")):
        return ["weather_agent"]
    if any(x in q for x in ("budget", "cost", "affordable", "expense", "expenses")):
        return ["budget_agent"]
    if any(x in q for x in ("itinerary", "day by day", "complete trip", "full trip")):
        return ["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"]
    return []


def supervisor_agent(state: TravelState):
    query = state["user_query"]
    llm_calls = state.get("llm_calls", 0)

    guardrail_prompt = f"""
Determine whether the following request belongs to travel planning or travel
information. Valid requests can include destinations, flights, hotels, weather,
budgets, visas, transportation, sightseeing, food, packing, or itineraries.

Block clearly unrelated requests and requests asking for harmful or illegal
instructions. Do not block a valid travel request merely because some details
are missing.

Return strict JSON only:
{{
  "allowed": true,
  "reason": ""
}}

User request:
{query}
"""

    try:
        guardrail_raw = _llm_text(
            "You are the input guardrail for a travel-planning application. Return strict JSON only.",
            guardrail_prompt,
        )
        guardrail_result = _json_from_llm(guardrail_raw)
        allowed = bool(guardrail_result.get("allowed", True))
        guardrail_reason = str(guardrail_result.get("reason", "")).strip()
        llm_calls += 1
    except Exception as exc:
        print(f"Guardrail fallback used: {exc}")
        allowed = True
        guardrail_reason = "Guardrail validation fallback allowed the request."

    if not allowed:
        reason = guardrail_reason or (
            "TripMate AI can only help with travel-planning requests. "
            "Please ask about a destination, flight, hotel, weather, budget, or itinerary."
        )
        return {
            "guardrail_allowed": False,
            "guardrail_reason": reason,
            "selected_agents": [],
            "trip_constraints": _empty_constraints(),
            "supervisor_reasoning": reason,
            "final_response": reason,
            "messages": [AIMessage(content=f"Guardrail blocked request: {reason}")],
            "llm_calls": llm_calls,
        }

    # Hard routing rule: "flight ... to USA" must never become JFK/New York.
    if _needs_destination_clarification(query):
        constraints = _empty_constraints()
        constraints["origin"] = "Delhi" if "delhi" in _normalized_query(query) else ""
        return {
            "guardrail_allowed": True,
            "guardrail_reason": guardrail_reason,
            "selected_agents": ["flight_agent"],
            "trip_constraints": constraints,
            "supervisor_reasoning": (
                "The request asks for a flight to the United States but does not "
                "specify a U.S. city or airport. Clarification is required."
            ),
            "messages": [AIMessage(content="Supervisor requires a destination clarification.")],
            "llm_calls": llm_calls,
        }

    supervisor_prompt = f"""
You are the supervisor of a multi-agent travel-planning system.

Determine EXACTLY which specialist agents are necessary for THE USER'S SPECIFIC REQUEST.
Do not select agents that are not needed. Do not create a full trip plan unless asked.

Available agents:
- flight_agent: flights, airfare, airlines, airports, routes, flight duration, booking advice.
- hotel_agent: hotels, accommodation, hostels, resorts, places to stay.
- weather_agent: weather, climate, forecast, seasonal conditions, weather packing.
- budget_agent: explicit total trip cost, affordability, budget feasibility, money-saving.
- itinerary_agent: ONLY an itinerary, day-by-day plan, complete trip plan, sightseeing
  planning, or a full travel plan combining multiple aspects.

Rules:
- Flight-only request -> ONLY flight_agent.
- Hotel-only request -> ONLY hotel_agent.
- Weather-only request -> ONLY weather_agent.
- Budget-only request -> ONLY budget_agent.
- Do not add itinerary_agent just because a destination is mentioned.
- If one option is requested, downstream output should contain one option.
- If the destination is a country with no specific city, do not invent a city.

Return strict JSON only:
{{
  "selected_agents": [],
  "trip_constraints": {{
    "destination": "",
    "origin": "",
    "duration": "",
    "budget": "",
    "travel_style": "",
    "special_preferences": []
  }},
  "reasoning": ""
}}

User request:
{query}
"""

    try:
        supervisor_raw = _llm_text(
            "You route work to travel specialist agents. Return strict JSON only.",
            supervisor_prompt,
        )
        parsed = _json_from_llm(supervisor_raw)
        requested_agents = parsed.get("selected_agents", [])
        selected_agents = [
            name for name in AGENT_ORDER
            if name in requested_agents and name in KNOWN_AGENTS
        ]

        constraints = _empty_constraints()
        parsed_constraints = parsed.get("trip_constraints", {})
        if isinstance(parsed_constraints, dict):
            constraints.update(parsed_constraints)

        reasoning = str(parsed.get("reasoning", "")).strip()
        llm_calls += 1

        if _is_flight_request(query) and "flight_agent" not in selected_agents:
            selected_agents = ["flight_agent"]
            reasoning = "Deterministic flight-intent validation selected flight_agent."
    except Exception as exc:
        print(f"Supervisor fallback used: {exc}")
        selected_agents = _fallback_agents(query)
        constraints = _empty_constraints()
        reasoning = (
            "Supervisor parsing failed, so deterministic intent routing selected "
            "only the agents required by the request."
        )

    return {
        "guardrail_allowed": True,
        "guardrail_reason": guardrail_reason,
        "selected_agents": selected_agents,
        "trip_constraints": constraints,
        "supervisor_reasoning": reasoning,
        "messages": [AIMessage(content="Supervisor created the agent plan.")],
        "llm_calls": llm_calls,
    }


def guardrail_blocked_agent(state: TravelState):
    reason = state.get("final_response") or state.get("guardrail_reason") or (
        "This request was blocked by the travel input guardrail."
    )
    return {"final_response": reason, "messages": [AIMessage(content=reason)]}


FLIGHT_AGENT_PROMPT = """
You are a travel flight expert.

User Query:
{query}

Airport Information:
{airport_data}

Airline Information:
{airline_data}

Answer ONLY the flight-related request.

Rules:
- Do not generate hotels, weather, itinerary, budget, or sightseeing content.
- If the user asks for ONE flight, return exactly ONE flight option.
- Do not silently choose a city when the user says only "USA" or "United States".
- For an ambiguous U.S. destination, ask which U.S. city or airport they want.
- Current tools may not provide live ticket fares. Never fabricate live prices or a
  "cheapest" fare. Clearly label any estimate as an estimate.
- Keep the response concise.
"""


def flight_agent(state: TravelState):
    print("\nINSIDE FLIGHT AGENT\n")
    query = state["user_query"]

    if _needs_destination_clarification(query):
        clarification = (
            "Sure — which city or airport in the United States do you want to fly to? "
            "For example: New York (JFK/EWR), Los Angeles (LAX), Chicago (ORD), "
            "San Francisco (SFO), or another U.S. destination."
        )
        return {
            "flight_results": clarification,
            "messages": [AIMessage(content=clarification)],
            "llm_calls": state.get("llm_calls", 0),
        }

    try:
        airports = asyncio.run(aviation_mcp_call("list_airports"))
        airlines = asyncio.run(aviation_mcp_call("list_airlines"))
        prompt = FLIGHT_AGENT_PROMPT.format(
            query=query,
            airport_data=str(airports)[:3000],
            airline_data=str(airlines)[:3000],
        )
        response = llm.invoke(
            [
                SystemMessage(content="You are an expert travel flight planner."),
                HumanMessage(content=prompt),
            ]
        )
        flight_data = response.content
    except Exception as exc:
        flight_data = f"Flight information unavailable: {exc}"

    return {
        "flight_results": flight_data,
        "messages": [AIMessage(content="Flight recommendations generated")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def hotel_agent(state: TravelState):
    query = f"Best hotels for {state['user_query']}"
    try:
        hotel_results = asyncio.run(tavily_mcp_search(query))
    except Exception as exc:
        print(f"HOTEL AGENT MCP ERROR: {type(exc).__name__}: {exc}", flush=True)
        hotel_results = (
            "Live hotel search is temporarily unavailable. Provide general "
            "accommodation and neighborhood guidance based on the destination "
            "and clearly label it as non-live advice."
        )
    return {
        "hotel_results": hotel_results,
        "messages": [AIMessage(content="Hotel information processed.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def weather_agent(state: TravelState):
    city = extract_destination(state["user_query"])
    try:
        weather_data = asyncio.run(weather_mcp_search(city))
        forecast_data = asyncio.run(forecast_mcp_search(city))
        weather_results = f"""
Current Weather:
{weather_data}

Forecast:
{forecast_data}
"""
    except Exception as exc:
        print(f"WEATHER AGENT MCP ERROR: {type(exc).__name__}: {exc}", flush=True)
        weather_results = (
            f"Live weather information for {city} is temporarily unavailable. "
            "Give general seasonal guidance and advise the traveler to verify the "
            "forecast before departure."
        )
    return {
        "weather_results": weather_results,
        "messages": [AIMessage(content="Weather information processed.")],
    }


def budget_agent(state: TravelState):
    prompt = f"""
Analyze whether this trip is realistic for the user's budget.

User Query:
{state['user_query']}

Trip Constraints:
{state.get('trip_constraints', {})}

Flight Results:
{state.get('flight_results', '')}

Hotel Results:
{state.get('hotel_results', '')}

Weather Results:
{state.get('weather_results', '')}

Return:
1. Estimated cost categories
2. Budget risk areas
3. Money-saving suggestions
4. Overall feasibility

If exact live prices are unavailable, clearly label estimates as approximate.
"""
    response = llm.invoke(
        [
            SystemMessage(content="You are a practical travel budget analyst."),
            HumanMessage(content=prompt),
        ]
    )
    return {
        "budget_results": response.content,
        "messages": [AIMessage(content="Budget assessment generated.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def itinerary_agent(state: TravelState):
    prompt = f"""
Create a complete travel itinerary.

User Query:
{state['user_query']}

Trip Constraints:
{state.get('trip_constraints', {})}

Flight Results:
{state.get('flight_results', '')}

Hotel Results:
{state.get('hotel_results', '')}

Weather Results:
{state.get('weather_results', '')}

Budget Results:
{state.get('budget_results', '')}

Make the itinerary practical, budget-aware, and easy to follow.
Create a clear draft that is ready for human review.
"""
    response = llm.invoke(
        [
            SystemMessage(content="You are an expert travel planner."),
            HumanMessage(content=prompt),
        ]
    )
    approval_request = (
        "Please review the generated draft itinerary. Approve it to create the "
        "final polished plan, or provide feedback for revision."
    )
    return {
        "itinerary": response.content,
        "approval_request": approval_request,
        "messages": [AIMessage(content="Draft itinerary created for human review.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def human_approval_agent(state: TravelState):
    review = interrupt(
        {
            "question": "Do you approve this itinerary?",
            "draft_itinerary": state.get("itinerary", ""),
            "approval_request": state.get("approval_request", ""),
            "selected_agents": state.get("selected_agents", []),
            "supervisor_reasoning": state.get("supervisor_reasoning", ""),
            "expected_response": {
                "approved": True,
                "feedback": "Optional revision feedback",
            },
        }
    )
    approved = bool(review.get("approved", False))
    human_feedback = str(review.get("feedback", "")).strip()
    return {
        "approved": approved,
        "human_feedback": human_feedback,
        "messages": [AIMessage(content="Human approval step completed.")],
    }


def final_agent(state: TravelState):
    query = state["user_query"]
    selected_agents = _selected_agents(state)

    if _needs_destination_clarification(query):
        clarification = (
            "Sure — which city or airport in the United States do you want to fly to? "
            "For example: New York (JFK/EWR), Los Angeles (LAX), Chicago (ORD), "
            "San Francisco (SFO), or another U.S. destination."
        )
        return {
            "final_response": clarification,
            "messages": [AIMessage(content=clarification)],
            "llm_calls": state.get("llm_calls", 0),
        }

    if state.get("approved", False):
        review_instruction = (
            "The user approved the draft. Preserve its decisions while polishing it."
        )
    elif "itinerary_agent" in selected_agents:
        review_instruction = f"""
The user requested a revision. Apply this feedback carefully:
{state.get('human_feedback', '') or 'Improve the draft before finalizing it.'}
"""
    else:
        review_instruction = "No human-review step was required for this focused request."

    final_prompt = f"""
Generate the final response for the user.

User Request:
{query}

Selected Agents:
{selected_agents}

Supervisor Constraints:
{state.get('trip_constraints', {})}

Flight Results:
{state.get('flight_results', '')}

Hotels:
{state.get('hotel_results', '')}

Weather:
{state.get('weather_results', '')}

Budget Analysis:
{state.get('budget_results', '')}

Draft Itinerary:
{state.get('itinerary', '')}

Human Review:
{review_instruction}

CRITICAL RESPONSE RULES:
1. Answer ONLY what the user asked.
2. Use ONLY information relevant to the selected agents and the user's request.
3. Do NOT add unrelated travel sections.
4. Do NOT mention or invent hotels when hotel_agent was not selected.
5. Do NOT mention or invent weather when weather_agent was not selected.
6. Do NOT create an itinerary when itinerary_agent was not selected.
7. Do NOT create a budget analysis when budget_agent was not selected.
8. If the user asks for ONE option, return exactly ONE option.
9. Do not call something cheapest unless reliable pricing data supports that claim.
10. Never turn an estimated fare into a claimed live ticket price.
11. If the destination is ambiguous, ask a concise clarification instead of choosing a city.
12. Focused questions should produce focused answers.
13. Incorporate human feedback only when an itinerary was actually created and reviewed.
"""
    response = llm.invoke(
        [
            SystemMessage(content="You are a professional AI travel booking assistant."),
            HumanMessage(content=final_prompt),
        ]
    )
    return {
        "final_response": response.content,
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


ROUTE_MAP = {
    "guardrail_blocked": "guardrail_blocked",
    "flight_agent": "flight_agent",
    "hotel_agent": "hotel_agent",
    "weather_agent": "weather_agent",
    "budget_agent": "budget_agent",
    "itinerary_agent": "itinerary_agent",
    "final_agent": "final_agent",
}


def _selected_agents(state: TravelState) -> list[str]:
    selected = state.get("selected_agents", [])
    return [agent for agent in AGENT_ORDER if agent in selected]


def route_from_supervisor(state: TravelState) -> str:
    if not state.get("guardrail_allowed", True):
        return "guardrail_blocked"
    selected = _selected_agents(state)
    return selected[0] if selected else "final_agent"


def route_after_agent(current_agent: str):
    def route(state: TravelState) -> str:
        selected = _selected_agents(state)
        current_index = AGENT_ORDER.index(current_agent)
        for next_agent in AGENT_ORDER[current_index + 1:]:
            if next_agent in selected:
                return next_agent
        if "itinerary_agent" in selected:
            return "itinerary_agent"
        return "final_agent"
    return route


graph = StateGraph(TravelState)
graph.add_node("supervisor", supervisor_agent)
graph.add_node("guardrail_blocked", guardrail_blocked_agent)
graph.add_node("flight_agent", flight_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("weather_agent", weather_agent)
graph.add_node("budget_agent", budget_agent)
graph.add_node("itinerary_agent", itinerary_agent)
graph.add_node("human_approval", human_approval_agent)
graph.add_node("final_agent", final_agent)

graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", route_from_supervisor, ROUTE_MAP)
graph.add_conditional_edges("flight_agent", route_after_agent("flight_agent"), ROUTE_MAP)
graph.add_conditional_edges("hotel_agent", route_after_agent("hotel_agent"), ROUTE_MAP)
graph.add_conditional_edges("weather_agent", route_after_agent("weather_agent"), ROUTE_MAP)
graph.add_conditional_edges("budget_agent", route_after_agent("budget_agent"), ROUTE_MAP)
graph.add_edge("itinerary_agent", "human_approval")
graph.add_edge("human_approval", "final_agent")
graph.add_edge("final_agent", END)
graph.add_edge("guardrail_blocked", END)

DATABASE_URL = get_database_url()
_conn = psycopg.connect(
    DATABASE_URL,
    autocommit=True,
    row_factory=dict_row,
)
checkpointer = PostgresSaver(_conn)
checkpointer.setup()
travel_graph = graph.compile(checkpointer=checkpointer)


def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__", [])
    if not interrupts:
        return None
    first_interrupt = interrupts[0]
    payload = getattr(first_interrupt, "value", first_interrupt)
    return payload if isinstance(payload, dict) else {"value": payload}


def _serialize_result(result: dict[str, Any], thread_id: str) -> dict[str, Any]:
    messages = result.get("messages", [])
    last_message = messages[-1].content if messages else ""
    answer = result.get("final_response") or last_message
    interrupt_payload = _interrupt_payload(result)

    if interrupt_payload:
        answer = interrupt_payload.get("draft_itinerary") or result.get("itinerary", "")

    return {
        "thread_id": thread_id,
        "answer": answer,
        "requires_approval": interrupt_payload is not None,
        "approval_request": (
            interrupt_payload.get("approval_request", "")
            if interrupt_payload
            else result.get("approval_request", "")
        ),
        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "weather_results": result.get("weather_results", ""),
        "budget_results": result.get("budget_results", ""),
        "itinerary": result.get("itinerary", ""),
        "selected_agents": result.get("selected_agents", []),
        "trip_constraints": result.get("trip_constraints", _empty_constraints()),
        "supervisor_reasoning": result.get("supervisor_reasoning", ""),
        "guardrail_allowed": result.get("guardrail_allowed", True),
        "guardrail_reason": result.get("guardrail_reason", ""),
        "approved": result.get("approved", False),
        "human_feedback": result.get("human_feedback", ""),
        "llm_calls": result.get("llm_calls", 0),
    }


def run_travel_agent(user_input: str, thread_id: str | None = None):
    thread_id = thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: TravelState = {
        "messages": [HumanMessage(content=user_input)],
        "user_query": user_input,
        "guardrail_allowed": True,
        "guardrail_reason": "",
        "selected_agents": [],
        "trip_constraints": _empty_constraints(),
        "supervisor_reasoning": "",
        "flight_results": "",
        "hotel_results": "",
        "weather_results": "",
        "itinerary": "",
        "budget_results": "",
        "approval_request": "",
        "approved": False,
        "human_feedback": "",
        "final_response": "",
        "llm_calls": 0,
    }

    result = travel_graph.invoke(initial_state, config=config)
    return _serialize_result(result, thread_id)


def resume_travel_agent(thread_id: str, approved: bool, feedback: str = ""):
    config = {"configurable": {"thread_id": thread_id}}
    result = travel_graph.invoke(
        Command(resume={"approved": approved, "feedback": feedback.strip()}),
        config=config,
    )
    return _serialize_result(result, thread_id)
