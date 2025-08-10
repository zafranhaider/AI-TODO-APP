# AI To-Do App (Flask) — Mini PRD & Deployment Notes

## Summary
A simple To-Do application built with Flask and SQLite that supports:
- Adding todos (title + optional description)
- Marking todos complete/incomplete
- Translating a todo into a user-specified language (name or ISO code) using LibreTranslate
- Generating subtasks using OpenAI (if `OPENAI_API_KEY` is provided) or a fallback rule-based generator
- Persistence via SQLAlchemy (SQLite)

## Tech stack
- Python + Flask
- SQLite via SQLAlchemy
- Optional: OpenAI API for AI-generated subtasks
- LibreTranslate public instance for translation (configurable)

## Files
- `app.py` — main Flask app and models
- `templates/` — Jinja2 templates (base, index, todo view)
- `static/styles.css` — simple styling
- `requirements.txt` — Python deps

## How translation works
- The app queries LibreTranslate `/languages` to match the user's input (name or code).
- It then calls `/translate` to translate the todo title+description.
- You can change `LIBRETRANSLATE_URL` env var to a private instance if desired.

## How AI subtasks work
- If `OPENAI_API_KEY` environment variable is set, the app will call OpenAI Chat Completions to request a JSON array of subtasks.
- If no key or call fails, a fallback generator produces 3–5 reasonable subtasks heuristically.

## Deployment
- Can be deployed on any Python-friendly host (Heroku, Render, Vercel (Serverless Python), etc.).
- Ensure `OPENAI_API_KEY` is set in environment if you want AI-generated subtasks.
- Ensure outbound network allowed for LibreTranslate & OpenAI endpoints.

## Deliverables checklist (for assessment form)
- Live deployment URL: (your deployment)
- GitHub repo: (your repo)
- Product Requirements Document: use this README as PRD
- User & Developer documentation: expand README with usage & API details
- Loom walkthrough: create a short recording showing features

## Notes & Extensions (optional)
- Add authentication to separate users.
- Add Flask-Migrate for migrations.
- Replace LibreTranslate with Google Translate API or DeepL for more reliable translation (requires API keys).
- Add tests.

