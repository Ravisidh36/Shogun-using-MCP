from pathlib import Path
import traceback
import asyncio
import uvicorn

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pydantic import BaseModel, Field

from backend import run_travel_agent, resume_travel_agent


# =========================================================
# PATHS
# =========================================================

BASE_DIR = Path(__file__).resolve().parent


# =========================================================
# FASTAPI APP
# =========================================================

app = FastAPI(
    title="TripMate AI",
    description=(
        "LangGraph Multi-Agent Travel Planner with Supervisor, "
        "Guardrails, Human-in-the-Loop, and FastAPI Frontend"
    ),
    version="2.0.0",
)


# =========================================================
# STATIC FILES
# =========================================================

app.mount(
    "/static",
    StaticFiles(
        directory=str(BASE_DIR / "static")
    ),
    name="static",
)


# =========================================================
# TEMPLATES
# =========================================================

templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)


# =========================================================
# REQUEST MODELS
# =========================================================

class TravelRequest(BaseModel):
    message: str
    thread_id: str | None = None


class ApprovalRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    approved: bool
    feedback: str = ""


# =========================================================
# HOME
# =========================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
async def home(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


# =========================================================
# START TRAVEL PLANNING
# =========================================================

@app.post("/api/travel")
async def travel_planner(
    request_data: TravelRequest
):

    try:

        user_message = request_data.message.strip()

        if not user_message:

            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Message cannot be empty.",
                },
            )


        # -------------------------------------------------
        # IMPORTANT
        #
        # run_travel_agent() is synchronous and internally
        # uses asyncio.run() for MCP calls.
        #
        # Run it in a worker thread so it gets its own
        # synchronous context instead of trying to nest
        # an event loop inside FastAPI's event loop.
        # -------------------------------------------------

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

        print(
            "TRAVEL ERROR:",
            exc,
            flush=True,
        )

        traceback.print_exc()


        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(exc),
            },
        )


# =========================================================
# HUMAN-IN-THE-LOOP APPROVAL
# =========================================================

@app.post("/api/travel/approve")
async def approve_travel_plan(
    request_data: ApprovalRequest
):

    try:

        feedback = (
            request_data.feedback.strip()
        )


        # -------------------------------------------------
        # Rejecting without feedback is not allowed.
        # -------------------------------------------------

        if (
            not request_data.approved
            and not feedback
        ):

            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": (
                        "Please provide revision feedback "
                        "when rejecting the draft."
                    ),
                },
            )


        # -------------------------------------------------
        # Resume LangGraph in a worker thread.
        # -------------------------------------------------

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

        print(
            "APPROVAL ERROR:",
            exc,
            flush=True,
        )

        traceback.print_exc()


        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(exc),
            },
        )


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health")
async def health_check():

    return {
        "status": "ok",

        "message":
            "TripMate AI API is running",

        "features": [
            "supervisor_agent",
            "input_guardrail",
            "human_in_the_loop",
            "budget_agent",
        ],
    }


# =========================================================
# FAVICON
# =========================================================

@app.get("/favicon.ico")
async def favicon():

    return JSONResponse(
        content={}
    )


# =========================================================
# LOCAL DEVELOPMENT
# =========================================================

if __name__ == "__main__":

    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )