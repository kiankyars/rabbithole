"""FastAPI dashboard for RabbitHole."""

import os
import json
import uuid
from datetime import datetime, timezone, date
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.home() / ".env")
load_dotenv(override=True)

from db import execute, execute_one, get_conn
from agent import run_cycle, run_cycle_all_users
from models import apply_schema
from ingest import run_from_bytes

import markdown as md


scheduler = BackgroundScheduler()
AGENT_STATUS = {"last_run": None, "running": False, "runs_completed": 0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_schema()
    scheduler.add_job(scheduled_research, "interval", hours=6, id="research_cycle")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="RabbitHole", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "rabbithole-hackathon-secret-2026"),
)
templates = Jinja2Templates(directory="templates")


def render_markdown(text: str) -> str:
    return md.markdown(text or "", extensions=["extra"])

templates.env.filters["markdown"] = render_markdown


def scheduled_research():
    """Background scheduled research cycle for all users."""
    AGENT_STATUS["running"] = True
    AGENT_STATUS["last_run"] = datetime.now(timezone.utc).isoformat()
    run_cycle_all_users(num_holes=5)
    AGENT_STATUS["running"] = False
    AGENT_STATUS["runs_completed"] += 1


def get_user(request: Request) -> dict | None:
    """Get current user from session."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return execute_one("SELECT * FROM users WHERE id = %s", (user_id,))


# --- Auth Routes ---


@app.get("/welcome", response_class=HTMLResponse)
async def welcome(request: Request):
    user = get_user(request)
    if user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("welcome.html", {"request": request})


@app.post("/signup")
async def signup(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    file: UploadFile = File(...),
):
    # Create user
    user_id = str(uuid.uuid4())
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (id, name) VALUES (%s, %s)",
        (user_id, name.strip()),
    )
    cur.close()
    conn.close()

    # Read file
    file_data = await file.read()

    # Quick insert of conversations (fast, ~1s)
    from ingest import parse_conversations_bytes, insert_conversations
    conversations = parse_conversations_bytes(file_data)
    insert_conversations(conversations, user_id=user_id)

    # Set session
    request.session["user_id"] = user_id

    # Background: classify rabbit holes + run first agent cycle
    background_tasks.add_task(_background_setup, file_data, user_id)

    return RedirectResponse("/", status_code=303)


def _background_setup(file_data: bytes, user_id: str):
    """Background task: extract rabbit holes then run first research cycle."""
    from ingest import parse_conversations_bytes, extract_rabbit_holes
    conversations = parse_conversations_bytes(file_data)
    extract_rabbit_holes(conversations, user_id=user_id)
    run_cycle(num_holes=5, user_id=user_id)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/welcome", status_code=303)


# --- Dashboard Routes ---


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_user(request)
    if not user:
        return RedirectResponse("/welcome", status_code=303)

    uid = user["id"]

    plan = execute_one(
        "SELECT plan_json, created_at FROM daily_plans WHERE user_id = %s AND plan_date = %s",
        (uid, date.today()),
    )

    holes = execute(
        """SELECT rh.id, rh.name, rh.description, rh.priority_score, rh.last_researched_at, rh.status,
                  COUNT(DISTINCT rhc.conversation_id) as conv_count,
                  (SELECT COUNT(*) FROM insights WHERE rabbit_hole_id = rh.id) as insight_count
           FROM rabbit_holes rh
           LEFT JOIN rabbit_hole_conversations rhc ON rh.id = rhc.rabbit_hole_id
           WHERE rh.status = 'active' AND rh.user_id = %s
           GROUP BY rh.id
           ORDER BY rh.priority_score DESC
           LIMIT 20""",
        (uid,),
        fetch=True,
    )

    recent_insights = execute(
        """SELECT i.content, i.urgency, i.created_at, rh.name as rabbit_hole_name
           FROM insights i
           JOIN rabbit_holes rh ON i.rabbit_hole_id = rh.id
           WHERE rh.user_id = %s
           ORDER BY i.created_at DESC LIMIT 10""",
        (uid,),
        fetch=True,
    )

    stats = execute_one(
        """SELECT
            (SELECT COUNT(*) FROM conversations WHERE user_id = %s) as total_conversations,
            (SELECT COUNT(*) FROM rabbit_holes WHERE status = 'active' AND user_id = %s) as active_holes,
            (SELECT COUNT(*) FROM insights i JOIN rabbit_holes rh ON i.rabbit_hole_id = rh.id WHERE rh.user_id = %s) as total_insights,
            (SELECT COUNT(*) FROM research_runs rr JOIN rabbit_holes rh ON rr.rabbit_hole_id = rh.id WHERE rh.user_id = %s) as total_runs""",
        (uid, uid, uid, uid),
    )

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "plan": plan,
        "holes": holes or [],
        "recent_insights": recent_insights or [],
        "stats": stats or {},
        "agent_status": AGENT_STATUS,
    })


@app.get("/rabbit-holes", response_class=HTMLResponse)
async def list_rabbit_holes(request: Request):
    user = get_user(request)
    if not user:
        return RedirectResponse("/welcome", status_code=303)

    holes = execute(
        """SELECT rh.id, rh.name, rh.description, rh.priority_score,
                  rh.last_researched_at, rh.status, rh.created_at,
                  COUNT(DISTINCT rhc.conversation_id) as conv_count,
                  (SELECT COUNT(*) FROM insights WHERE rabbit_hole_id = rh.id) as insight_count
           FROM rabbit_holes rh
           LEFT JOIN rabbit_hole_conversations rhc ON rh.id = rhc.rabbit_hole_id
           WHERE rh.user_id = %s
           GROUP BY rh.id
           ORDER BY rh.priority_score DESC""",
        (user["id"],),
        fetch=True,
    )
    return templates.TemplateResponse("rabbit_holes.html", {
        "request": request,
        "user": user,
        "holes": holes or [],
    })


@app.get("/rabbit-holes/{hole_id}", response_class=HTMLResponse)
async def rabbit_hole_detail(request: Request, hole_id: int):
    user = get_user(request)
    if not user:
        return RedirectResponse("/welcome", status_code=303)

    hole = execute_one(
        "SELECT * FROM rabbit_holes WHERE id = %s AND user_id = %s",
        (hole_id, user["id"]),
    )
    if not hole:
        return RedirectResponse("/", status_code=303)

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
        "SELECT * FROM insights WHERE rabbit_hole_id = %s ORDER BY created_at DESC",
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
        "user": user,
        "hole": hole,
        "conversations": conversations or [],
        "insights": insights or [],
        "runs": runs or [],
    })


@app.post("/agent/run")
async def trigger_agent(request: Request, background_tasks: BackgroundTasks):
    user = get_user(request)
    if not user:
        return JSONResponse({"status": "unauthorized"}, status_code=401)
    if AGENT_STATUS["running"]:
        return JSONResponse({"status": "already_running"})

    def _run_for_user():
        AGENT_STATUS["running"] = True
        AGENT_STATUS["last_run"] = datetime.now(timezone.utc).isoformat()
        run_cycle(num_holes=5, user_id=user["id"])
        AGENT_STATUS["running"] = False
        AGENT_STATUS["runs_completed"] += 1

    background_tasks.add_task(_run_for_user)
    return JSONResponse({"status": "started"})


@app.get("/agent/status")
async def agent_status():
    return JSONResponse(AGENT_STATUS)


@app.get("/api/rabbit-holes")
async def api_rabbit_holes(request: Request):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    holes = execute(
        """SELECT rh.id, rh.name, rh.description, rh.priority_score,
                  rh.last_researched_at, rh.status
           FROM rabbit_holes rh WHERE rh.status = 'active' AND rh.user_id = %s
           ORDER BY rh.priority_score DESC""",
        (user["id"],),
        fetch=True,
    )
    return JSONResponse([dict(r) for r in (holes or [])], default=str)


@app.get("/api/insights")
async def api_insights(request: Request, limit: int = 20):
    user = get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    insights = execute(
        """SELECT i.*, rh.name as rabbit_hole_name
           FROM insights i JOIN rabbit_holes rh ON i.rabbit_hole_id = rh.id
           WHERE rh.user_id = %s
           ORDER BY i.created_at DESC LIMIT %s""",
        (user["id"], limit),
        fetch=True,
    )
    return JSONResponse([dict(r) for r in (insights or [])], default=str)
