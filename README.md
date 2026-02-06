# RabbitHole: Autonomous Personal Knowledge Agent

> Upload your ChatGPT history. An AI agent finds your rabbit holes, researches them while you sleep, and tells you what to do every morning.

**Live:** [rabbithole-td04.onrender.com](https://rabbithole-td04.onrender.com)

---

## The Problem

You've had hundreds of conversations with ChatGPT -- deep dives into reinforcement learning, half-finished side projects, languages you started learning, papers you meant to read. But that knowledge is trapped in a chat log. You never go back. You never follow through.

## What RabbitHole Does

1. **Ingests** your full ChatGPT export (conversations.json)
2. **Extracts rabbit holes** -- recurring topics you've obsessed over -- using DeepSeek V3.2
3. **Autonomously researches** each rabbit hole every 6 hours: generates search queries, grounds them with live web results, synthesizes new insights
4. **Generates a daily action plan** prioritized by urgency, recency, and depth
5. **Runs without you** -- no prompting, no manual triggers, no intervention

## Architecture

```
conversations.json
       |
       v
  [DeepSeek V3.2 via Akash ML] -- classify into rabbit holes
       |
       v
  [Postgres on Render] -- conversations, rabbit holes, insights, plans
       |
       v
  [Autonomous Agent Loop - every 6h]
       |
       +---> DeepSeek generates search queries
       +---> You.com Search grounds them with real-time web data
       +---> DeepSeek synthesizes insights + urgency scores
       +---> Daily action plan regenerated
       |
       v
  [FastAPI Dashboard on Render] -- view rabbit holes, insights, daily plan
```

## Sponsor Tools Used

| Tool | How It's Used |
|------|--------------|
| **Akash ML** | DeepSeek V3.2 inference -- classifies conversations, generates research queries, synthesizes insights, writes daily plans |
| **Render** | Managed Postgres for all persistent data + web service hosting with auto-deploy from GitHub |
| **You.com Search API** | Real-time web search to ground every AI-generated insight with current sources |

## Autonomy

- The agent runs on a 6-hour schedule with zero human input
- Each cycle: picks the stalest high-priority rabbit holes, generates fresh queries, searches the live web, synthesizes findings, scores urgency, and rebuilds the daily plan
- New users just upload a file and walk away -- the agent handles everything from classification to first research cycle in the background

## Quick Start

```bash
git clone https://github.com/kiankyars/rabbithole
cd rabbithole
cp .env.example .env  # fill in API keys
uv sync
uv run python models.py          # create tables
uv run python ingest.py conversations.json  # ingest your history
uv run python agent.py            # run first research cycle
uv run uvicorn main:app --port 8000  # start dashboard
```

## Stack

Python, FastAPI, PostgreSQL, DeepSeek V3.2 (Akash ML), You.com Search API, Render, uv
