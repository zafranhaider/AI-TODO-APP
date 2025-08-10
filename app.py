"""
AI To-Do App (Flask)
Features:
- Create todos (title, description, optional target language for translation)
- Mark todos completed/uncompleted
- Translate todo text to a user-provided language (name or ISO code) using LibreTranslate public instance
- Generate subtasks via OpenAI (if OPENAI_API_KEY set) or fallback heuristic generator
- Persist todos and subtasks in SQLite via SQLAlchemy

Run:
    pip install -r requirements.txt
    export FLASK_APP=app.py
    export FLASK_ENV=development
    export OPENAI_API_KEY=sk-...   # optional; if not present, fallback used
    flask run
"""

import os
import json
from datetime import datetime
from typing import List, Optional

from flask import (
    Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
import requests
from dotenv import load_dotenv

load_dotenv()

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()
LIBRETRANSLATE_URL = os.getenv("LIBRETRANSLATE_URL", "https://libretranslate.com")  # can override

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET", "dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///todo_ai.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# ---------- Models ----------
class ToDo(db.Model):
    __tablename__ = "todos"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed = db.Column(db.Boolean, default=False)
    translated_text = db.Column(db.Text, nullable=True)
    translated_lang = db.Column(db.String(50), nullable=True)  # the target language the user asked for

    subtasks = db.relationship("SubTask", backref="todo", cascade="all, delete-orphan", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "completed": self.completed,
            "created_at": self.created_at.isoformat(),
            "translated_text": self.translated_text,
            "translated_lang": self.translated_lang,
            "subtasks": [s.to_dict() for s in self.subtasks.order_by(SubTask.id).all()]
        }


class SubTask(db.Model):
    __tablename__ = "subtasks"
    id = db.Column(db.Integer, primary_key=True)
    todo_id = db.Column(db.Integer, db.ForeignKey("todos.id"), nullable=False)
    text = db.Column(db.String(500), nullable=False)
    done = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {"id": self.id, "todo_id": self.todo_id, "text": self.text, "done": self.done}


# Initialize DB (create tables)
with app.app_context():
    db.create_all()


# ---------- Utilities ----------
def generate_subtasks_via_openai(prompt: str, max_subtasks: int = 5) -> List[str]:
    """
    Attempts to generate subtasks using OpenAI Chat Completion (if OPENAI_KEY set).
    Otherwise raises an exception to be caught by caller.
    """
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY not provided")

    # Use Chat Completions API (requests). The example uses a generic model name; users can change as needed.
    endpoint = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }
    system_prompt = (
        "You are an assistant that converts a single to-do item into a concise ordered list of "
        "clear subtasks. Output only a JSON array of subtasks (strings)."
    )
    user_prompt = (
        f"To-do item: {prompt}\n\n"
        f"Return up to {max_subtasks} subtasks as a JSON array. Keep items short (under 80 chars each)."
    )

    payload = {
        "model": "gpt-4o-mini",  # user can change; keep flexible
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 300,
        "temperature": 0.6,
        "n": 1,
    }

    r = requests.post(endpoint, headers=headers, json=payload, timeout=20)
    r.raise_for_status()
    j = r.json()
    # extract assistant content
    assistant_text = j["choices"][0]["message"]["content"].strip()

    # We expect JSON array. Try parse; if not JSON, attempt to extract array-like text.
    try:
        arr = json.loads(assistant_text)
        if isinstance(arr, list):
            return [str(x).strip() for x in arr if str(x).strip()]
    except Exception:
        # fallback: try to extract lines and return them
        lines = [ln.strip("- .\t") for ln in assistant_text.splitlines() if ln.strip()]
        if lines:
            return lines[:max_subtasks]

    # If still nothing, raise
    raise RuntimeError("OpenAI response not parseable")


def generate_subtasks_fallback(text: str, max_subtasks: int = 5) -> List[str]:
    """
    Simple heuristic fallback to generate subtasks from todo text.
    """
    # naive heuristics: split by colon/comma/then verbs; if short, create generic steps
    text = text.strip()
    out = []

    # If text already contains numbers / steps, try splitting
    if "\n" in text:
        parts = [p.strip() for p in text.splitlines() if p.strip()]
        out = parts[:max_subtasks]

    if not out:
        # break by common separators
        for sep in [":", "-", "—", ";", ","]:
            if sep in text:
                parts = [p.strip() for p in text.split(sep) if p.strip()]
                if len(parts) > 1:
                    out = parts[:max_subtasks]
                    break

    if not out:
        # Generic template: research -> plan -> implement -> test -> review
        templates = [
            "Research / gather requirements",
            "Break down tasks and estimate time",
            "Implement main functionality",
            "Test and fix bugs",
            "Deploy / finalize"
        ]
        # try to customize first template with the text's action noun
        first = f"Start: {text}" if len(text) < 80 else text[:80]
        out = [first] + templates[: max_subtasks - 1]

    # sanitize
    out = [o for o in out if o and len(o) < 300][:max_subtasks]
    return out


def translate_text(text: str, target_lang: str) -> Optional[str]:
    """
    Translate text using LibreTranslate public instance.
    target_lang: user provided language name or ISO code (e.g., 'en', 'fr', 'german').
    Returns translated text or None on failure.
    """
    if not text:
        return None

    # LibreTranslate /languages expects codes; the service supports a set of languages.
    # We'll attempt to map name to code by querying /languages.
    try:
        resp = requests.get(f"{LIBRETRANSLATE_URL}/languages", timeout=8)
        resp.raise_for_status()
        langs = resp.json()  # list of {code, name}
    except Exception:
        langs = []

    # Normalize user input
    t = target_lang.strip().lower()
    code = None
    for item in langs:
        if item.get("code", "").lower() == t or item.get("name", "").lower() == t:
            code = item.get("code")
            break
    if not code:
        # if user gave 2-letter, use it
        if len(t) in (2, 3):
            code = t
        else:
            # fallback: try first word (e.g., 'portuguese (brazil)' -> 'portuguese')
            first = t.split()[0]
            if len(first) in (2, 3):
                code = first
            else:
                # fail gracefully
                code = t

    try:
        payload = {"q": text, "source": "auto", "target": code, "format": "text"}
        r = requests.post(f"{LIBRETRANSLATE_URL}/translate", json=payload, timeout=12)
        r.raise_for_status()
        data = r.json()
        return data.get("translatedText")
    except Exception:
        return None


# ---------- Routes ----------
@app.route("/")
def index():
    todos = ToDo.query.order_by(ToDo.created_at.desc()).all()
    return render_template("index.html", todos=todos)


@app.route("/todo/create", methods=["POST"])
def create_todo():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    target_lang = request.form.get("target_lang", "").strip()

    if not title:
        flash("Title is required.", "danger")
        return redirect(url_for("index"))

    todo = ToDo(title=title, description=description)
    db.session.add(todo)
    db.session.commit()

    # Translate if requested
    if target_lang:
        translated = translate_text(f"{title}\n\n{description}", target_lang)
        if translated:
            todo.translated_text = translated
            todo.translated_lang = target_lang
            db.session.commit()

    flash("Todo created.", "success")
    return redirect(url_for("view_todo", todo_id=todo.id))


@app.route("/todo/<int:todo_id>")
def view_todo(todo_id):
    todo = ToDo.query.get_or_404(todo_id)
    return render_template("todo_view.html", todo=todo)


@app.route("/todo/<int:todo_id>/toggle", methods=["POST"])
def toggle_complete(todo_id):
    todo = ToDo.query.get_or_404(todo_id)
    todo.completed = not todo.completed
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/todo/<int:todo_id>/generate_subtasks", methods=["POST"])
def generate_subtasks(todo_id):
    todo = ToDo.query.get_or_404(todo_id)
    max_n = int(request.form.get("max_subtasks", 5))
    source_text = f"{todo.title}. {todo.description or ''}"

    subtasks = []
    # Try OpenAI if key provided
    if OPENAI_KEY:
        try:
            subtasks = generate_subtasks_via_openai(source_text, max_subtasks=max_n)
        except Exception as e:
            # fallback to fallback generator
            subtasks = generate_subtasks_fallback(source_text, max_subtasks=max_n)
    else:
        subtasks = generate_subtasks_fallback(source_text, max_subtasks=max_n)

    # Save subtasks
    for s in subtasks:
        st = SubTask(todo_id=todo.id, text=s)
        db.session.add(st)
    db.session.commit()
    flash(f"Added {len(subtasks)} subtasks.", "success")
    return redirect(url_for("view_todo", todo_id=todo.id))


@app.route("/subtask/<int:subtask_id>/toggle", methods=["POST"])
def toggle_subtask(subtask_id):
    st = SubTask.query.get_or_404(subtask_id)
    st.done = not st.done
    db.session.commit()
    return redirect(url_for("view_todo", todo_id=st.todo_id))


@app.route("/todo/<int:todo_id>/delete", methods=["POST"])
def delete_todo(todo_id):
    todo = ToDo.query.get_or_404(todo_id)
    db.session.delete(todo)
    db.session.commit()
    flash("Todo deleted.", "info")
    return redirect(url_for("index"))


@app.route("/api/todo/<int:todo_id>/translate", methods=["POST"])
def api_translate(todo_id):
    """
    API to translate a todo to a user-specified language (JSON POST: {"target":"french"}).
    Returns JSON with translated text or error.
    """
    todo = ToDo.query.get_or_404(todo_id)
    data = request.get_json(force=True)
    target = data.get("target", "").strip()
    if not target:
        return jsonify({"error": "target language required"}), 400
    translated = translate_text(f"{todo.title}\n\n{todo.description or ''}", target)
    if not translated:
        return jsonify({"error": "translation failed"}), 500
    todo.translated_text = translated
    todo.translated_lang = target
    db.session.commit()
    return jsonify({"translated": translated, "target": target})


# Minimal static route (optional)
@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


# ---------- Run ----------
if __name__ == "__main__":
    # For direct run during development
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
