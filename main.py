"""FastAPI dashboard for RabbitHole."""

import os
import json
from datetime import datetime, timezone, date
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.background import BackgroundScheduler
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.home() / ".env")
load_dotenv(override=True)

from db import execute, execute_one
from agent import run_cycle
from models import apply_schema


scheduler = BackgroundScheduler()
AGENT_STATUS = {"last_run": None, "running": False, "runs_completed": 0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: apply schema + start scheduler
    apply_schema()
    scheduler.add_job(scheduled_research, "interval", hours=6, id="research_cycle")
    scheduler.start()
    yield
    # Shutdown
    scheduler.shutdown()


app = FastAPI(title="RabbitHole", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

import markdown as md

def render_markdown(text: str) -> str:
    return md.markdown(text or "", extensions=["extra"])

templates.env.filters["markdown"] = render_markdown


def scheduled_research():
    """Background scheduled research cycle."""
    AGENT_STATUS["running"] = True
    AGENT_STATUS["last_run"] = datetime.now(timezone.utc).isoformat()
    run_cycle(num_holes=5)
    AGENT_STATUS["running"] = False
    AGENT_STATUS["runs_completed"] += 1


# --- Dashboard Routes ---


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Today's plan
    plan = execute_one(
        "SELECT plan_json, created_at FROM daily_plans WHERE plan_date = %s",
        (date.today(),),
    )

    # Top rabbit holes
    holes = execute(
        """SELECT rh.id, rh.name, rh.description, rh.priority_score, rh.last_researched_at, rh.status,
                  COUNT(DISTINCT rhc.conversation_id) as conv_count,
                  (SELECT COUNT(*) FROM insights WHERE rabbit_hole_id = rh.id) as insight_count
           FROM rabbit_holes rh
           LEFT JOIN rabbit_hole_conversations rhc ON rh.id = rhc.rabbit_hole_id
           WHERE rh.status = 'active'
           GROUP BY rh.id
           ORDER BY rh.priority_score DESC
           LIMIT 20""",
        fetch=True,
    )

    # Recent insights
    recent_insights = execute(
        """SELECT i.content, i.urgency, i.created_at, rh.name as rabbit_hole_name
           FROM insights i
           JOIN rabbit_holes rh ON i.rabbit_hole_id = rh.id
           ORDER BY i.created_at DESC LIMIT 10""",
        fetch=True,
    )

    # Stats
    stats = execute_one(
        """SELECT
            (SELECT COUNT(*) FROM conversations) as total_conversations,
            (SELECT COUNT(*) FROM messages) as total_messages,
            (SELECT COUNT(*) FROM rabbit_holes WHERE status = 'active') as active_holes,
            (SELECT COUNT(*) FROM insights) as total_insights,
            (SELECT COUNT(*) FROM research_runs) as total_runs"""
    )

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "plan": plan,
        "holes": holes or [],
        "recent_insights": recent_insights or [],
        "stats": stats or {},
        "agent_status": AGENT_STATUS,
    })


@app.get("/rabbit-holes", response_class=HTMLResponse)
async def list_rabbit_holes(request: Request):
    holes = execute(
        """SELECT rh.id, rh.name, rh.description, rh.priority_score,
                  rh.last_researched_at, rh.status, rh.created_at,
                  COUNT(DISTINCT rhc.conversation_id) as conv_count,
                  (SELECT COUNT(*) FROM insights WHERE rabbit_hole_id = rh.id) as insight_count
           FROM rabbit_holes rh
           LEFT JOIN rabbit_hole_conversations rhc ON rh.id = rhc.rabbit_hole_id
           GROUP BY rh.id
           ORDER BY rh.priority_score DESC""",
        fetch=True,
    )
    return templates.TemplateResponse("rabbit_holes.html", {
        "request": request,
        "holes": holes or [],
    })


@app.get("/rabbit-holes/{hole_id}", response_class=HTMLResponse)
async def rabbit_hole_detail(request: Request, hole_id: int):
    hole = execute_one("SELECT * FROM rabbit_holes WHERE id = %s", (hole_id,))

    conversations = execute(
        """SELECT c.id, c.title, c.created_at, c.message_count
           FROM conversations c
           JOIN rabbit_hole_conversations rhc ON c.id = rhc.conversation_id
           WHERE rhc.rabbit_hole_id = %s
           ORDER BY c.created_at DESC""",
        (hole_id,),
        fetch=True,
    )

    insights = execute(
        """SELECT * FROM insights WHERE rabbit_hole_id = %s ORDER BY created_at DESC""",
        (hole_id,),
        fetch=True,
    )

    runs = execute(
        """SELECT id, query_sent, created_at FROM research_runs
           WHERE rabbit_hole_id = %s ORDER BY created_at DESC LIMIT 10""",
        (hole_id,),
        fetch=True,
    )

    return templates.TemplateResponse("rabbit_hole.html", {
        "request": request,
        "hole": hole,
        "conversations": conversations or [],
        "insights": insights or [],
        "runs": runs or [],
    })


@app.post("/agent/run")
async def trigger_agent(background_tasks: BackgroundTasks):
    if AGENT_STATUS["running"]:
        return JSONResponse({"status": "already_running"})
    background_tasks.add_task(scheduled_research)
    return JSONResponse({"status": "started"})


@app.get("/agent/status")
async def agent_status():
    return JSONResponse(AGENT_STATUS)


@app.get("/api/rabbit-holes")
async def api_rabbit_holes():
    holes = execute(
        """SELECT rh.id, rh.name, rh.description, rh.priority_score,
                  rh.last_researched_at, rh.status
           FROM rabbit_holes rh WHERE rh.status = 'active'
           ORDER BY rh.priority_score DESC""",
        fetch=True,
    )
    return JSONResponse([dict(r) for r in (holes or [])], default=str)


@app.get("/api/insights")
async def api_insights(limit: int = 20):
    insights = execute(
        """SELECT i.*, rh.name as rabbit_hole_name
           FROM insights i JOIN rabbit_holes rh ON i.rabbit_hole_id = rh.id
           ORDER BY i.created_at DESC LIMIT %s""",
        (limit,),
        fetch=True,
    )
    return JSONResponse([dict(r) for r in (insights or [])], default=str)
