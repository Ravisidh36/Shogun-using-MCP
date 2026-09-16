from pathlib import Path
import traceback
import asyncio
import re
import uvicorn

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from backend import run_travel_agent, resume_travel_agent


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="TripMate AI",
    description=(
        "LangGraph Multi-Agent Travel Planner with Supervisor, "
        "Guardrails, Human-in-the-Loop, and FastAPI Frontend"
    ),
    version="2.0.0",
)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class TravelRequest(BaseModel):
    message: str
    thread_id: str | None = None


class ApprovalRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    approved: bool
    feedback: str = ""


def _normalize_user_message(message: str) -> str:
    """
    The frontend supports both structured fields and a free-form request.
    If a user types a focused request in the free-form field while an old
    destination remains selected, composeQuery() can produce text such as:

        Plan a trip to New York ... Additional notes: Find me a flight from Delhi to USA

    The free-form request is the user's actual instruction, so prefer it for
    focused flight/hotel/weather/budget queries. This prevents stale structured
    form values from changing the user's intended destination.
    """
    text = message.strip()
    marker = "additional notes:"
    lower = text.lower()

    if marker not in lower:
        return text

    idx = lower.rfind(marker)
    notes = text[idx + len(marker):].strip()

    if not notes:
        return text

    notes_lower = notes.lower()
    focused_terms = (
        "flight", "flights", "airfare", "airline", "hotel", "hostel",
        "accommodation", "weather", "forecast", "budget", "cost"
    )

    if any(term in notes_lower for term in focused_terms):
        return notes

    return text


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.post("/api/travel")
async def travel_planner(request_data: TravelRequest):
    try:
        user_message = _normalize_user_message(request_data.message)

        if not user_message:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Message cannot be empty.",
                },
            )

        result = await asyncio.to_thread(
            run_travel_agent,
            user_input=user_message,
            thread_id=request_data.thread_id,
        )

        return JSONResponse(
            content={
                "success": True,
                **result,
            }
        )

    except Exception as exc:
        print("TRAVEL ERROR:", exc, flush=True)
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(exc),
            },
        )


@app.post("/api/travel/approve")
async def approve_travel_plan(request_data: ApprovalRequest):
    try:
        feedback = request_data.feedback.strip()

        if not request_data.approved and not feedback:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Please provide revision feedback when rejecting the draft.",
                },
            )

        result = await asyncio.to_thread(
            resume_travel_agent,
            thread_id=request_data.thread_id,
            approved=request_data.approved,
            feedback=feedback,
        )

        return JSONResponse(
            content={
                "success": True,
                **result,
            }
        )

    except Exception as exc:
        print("APPROVAL ERROR:", exc, flush=True)
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(exc),
            },
        )


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "message": "TripMate AI API is running",
        "features": [
            "supervisor_agent",
            "input_guardrail",
            "human_in_the_loop",
            "budget_agent",
        ],
    }


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
