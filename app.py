from pathlib import Path
import traceback
import uvicorn

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from backend import run_travel_agent
from utils.pdf_export import build_briefing_pdf
from utils.errors import to_client_error


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Shogun",
    description="LangGraph Multi-Agent Travel Planner with FastAPI Frontend",
    version="1.0.0"
)


app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static"
)


templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)



class TravelRequest(BaseModel):
    message: str
    thread_id: str | None = None


class PdfRequest(BaseModel):
    answer: str
    thread_id: str | None = None



@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.post("/api/travel")
async def travel_planner(request_data: TravelRequest):
    try:
        user_message = request_data.message.strip()

        if not user_message:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": {
                        "code": "EMPTY_MESSAGE",
                        "message": "Please describe the trip you'd like planned.",
                        "retryable": False,
                    }
                }
            )

        result = await run_travel_agent(
            user_input=user_message,
            thread_id=request_data.thread_id
        )

        return JSONResponse(
            content={
                "success": True,
                "thread_id": result["thread_id"],
                "answer": result["answer"],
                "flight_results": result["flight_results"],
                "flight_data": result.get("flight_data"),
                "hotel_results": result["hotel_results"],
                "weather_results": result["weather_results"],
                "weather_data": result.get("weather_data"),
                "itinerary": result["itinerary"],
                "llm_calls": result["llm_calls"],
            }
        )

    except Exception as e:
        print("ERROR:", e)
        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": to_client_error(e)
            }
        )



@app.post("/api/travel/pdf")
async def travel_pdf(request_data: PdfRequest):
    try:
        pdf_bytes = build_briefing_pdf(
            request_data.answer,
            thread_id=request_data.thread_id
        )

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="shogun-travel-briefing.pdf"'
            }
        )

    except Exception as e:
        print("PDF ERROR:", e)
        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": to_client_error(e)
            }
        )



@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "message": "AI Travel Planner API is running"
    }


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})



if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )