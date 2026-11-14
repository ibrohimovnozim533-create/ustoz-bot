"""Ustoz — kurs oʻqituvchi Telegram bot (bitta fayl, faqat httpx).

Bulut (Render) uchun: python bot.py
Health server: GET /health -> ok
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

# ==================== config ====================
# bitta fayl: loyiha papkasi — faylning oʻzi turgan papka
BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    """.env faylini o'qib, muhitga qo'shadi (mavjudlarini bosmaydi)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _ids(raw: str) -> set[int]:
    out: set[int] = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


def _chain(raw: str) -> list[str]:
    items = [p.strip().lower() for p in (raw or "").replace(";", ",").split(",")]
    return [p for p in items if p]


def _path(env: str, default: str) -> Path:
    """Nisbiy yoʻlni loyiha papkasiga bogʻlaydi (CWD qayerda boʻlsa ham)."""
    path = Path(os.getenv(env, default))
    return path if path.is_absolute() else (BASE_DIR / path)


@dataclass
class Config:
    bot_token: str
    course_dir: Path
    db_path: Path
    llm_chain: list[str] = field(default_factory=lambda: ["gemini"])

    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-lite-latest"
    gemini_models: list[str] = field(
        default_factory=lambda: ["gemini-flash-lite-latest"]
    )
    gemini_thinking: int = 0

    openai_api_key: str = ""
    openai_base: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    groq_api_key: str = ""
    groq_base: str = "https://api.groq.com/openai/v1"
    groq_model: str = "llama-3.3-70b-versatile"

    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"

    admin_ids: set[int] = field(default_factory=set)
    port: int = 0
    language: str = "uz-latn"


def load() -> Config:
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest").strip()
    gemini_models = _chain(os.getenv("GEMINI_MODELS", "")) or [gemini_model]
    cfg = Config(
        bot_token=os.getenv("BOT_TOKEN", "").strip(),
        course_dir=_path("COURSE_DIR", "course"),
        db_path=_path("DB_PATH", "bot.db"),
        llm_chain=_chain(os.getenv("LLM", "gemini")),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=gemini_model,
        gemini_models=gemini_models,
        gemini_thinking=int(os.getenv("GEMINI_THINKING", "0") or 0),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_base=os.getenv("OPENAI_BASE", "https://api.openai.com/v1").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_base=os.getenv("GROQ_BASE", "https://api.groq.com/openai/v1").strip(),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip(),
        ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").strip(),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b").strip(),
        admin_ids=_ids(os.getenv("ADMIN_IDS", "")),
        port=int(os.getenv("PORT", "0") or 0),
        language=os.getenv("BOT_LANGUAGE", "uz-latn").strip(),
    )
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.course_dir.mkdir(parents=True, exist_ok=True)
    return cfg

# ==================== latin ====================
_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ғ": "gʻ", "д": "d",
    "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y",
    "к": "k", "қ": "q", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ў": "oʻ",
    "ф": "f", "х": "x", "ҳ": "h", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "sh", "ъ": "ʼ", "ы": "i", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "ө": "oʻ",
}


_SPECIAL_CYR = {
    "aksiya": "акция", "aksiyador": "акциядор", "obligatsiya": "облигация",
    "dividend": "дивиденд", "broker": "брокер", "tiker": "тикер",
    "emitent": "эмитент", "investor": "инвестор", "birja": "биржа",
    "depozitariy": "депозитарий", "fond": "фонд", "bozor": "бозор",
    "savdo": "савдо", "narx": "нарх", "foyda": "фойда", "zarar": "зарар",
    "risk": "риск", "portfel": "портфель", "strategiya": "стратегия",
    "analiz": "анализ", "hisob": "ҳисоб", "pul": "пул", "bank": "банк",
    "kredit": "кредит", "soliq": "солиқ", "qarz": "қарз", "valyuta": "валюта",
    "aksiy": "акция", "aksiyalar": "акциялар", "daromad": "даромад",
}

_CYR = {
    "a": "а", "b": "б", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "ҳ",
    "i": "и", "j": "ж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о",
    "p": "п", "q": "қ", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в",
    "x": "х", "y": "й", "z": "з", "ʼ": "ъ", "'": "ь",
}

_DIGRAPHS = (
    ("oʻ", "ў"), ("o'", "ў"), ("o‘", "ў"),
    ("gʻ", "ғ"), ("g'", "ғ"), ("g‘", "ғ"),
    ("sh", "ш"), ("ch", "ч"), ("yo", "ё"), ("yu", "ю"), ("ya", "я"),
    ("ts", "ц"),
)


def to_cyrillic(text: str) -> str:
    """Lotin yozuvidagi soʻrovni kirill indeksiga oʻgirish (qidiruv uchun)."""
    low = (text or "").lower()
    for latin, cyr in _SPECIAL_CYR.items():
        low = low.replace(latin, cyr)
    out: list[str] = []
    i = 0
    while i < len(low):
        for lat, cyr in _DIGRAPHS:
            if low.startswith(lat, i):
                out.append(cyr)
                i += len(lat)
                break
        else:
            ch = low[i]
            out.append(_CYR.get(ch, ch))
            i += 1
    return "".join(out)


_LATIN_FIX = {
    "aktsiya": "aksiya", "Aktsiya": "Aksiya",
    "aktsiyalar": "aksiyalar", "aktsiyador": "aksiyador",
    "divident": "dividend", "Divident": "Dividend",
    "hioblanadi": "hisoblanadi", "hiob": "hisob",
    "obligatsya": "obligatsiya", "emitentni": "emitentni",
}


def to_latin(text: str) -> str:
    # Imlo istisnolari: "акция" -> "aksiya" (ts emas)
    text = text.replace("акция", "aksiya").replace("Акция", "Aksiya")
    out: list[str] = []
    for idx, ch in enumerate(text):
        rep = _MAP.get(ch.lower())
        if rep is None:
            out.append(ch)
            continue
        if ch.isupper():
            rep = rep[:1].upper() + rep[1:]
        # soʻz boshidagi "е" — "ye"
        if ch.lower() == "е" and (idx == 0 or not text[idx - 1].isalnum()):
            rep = ("Y" if ch.isupper() else "y") + rep
        out.append(rep)
    text = "".join(out)
    for wrong, right in _LATIN_FIX.items():
        text = text.replace(wrong, right)
    return text

# ==================== db ====================
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    name       TEXT,
    username   TEXT,
    created_at REAL NOT NULL,
    last_seen  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS progress (
    user_id    INTEGER NOT NULL,
    module_id  INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'new',
    score      INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, module_id)
);
CREATE TABLE IF NOT EXISTS lessons (
    module_id  INTEGER PRIMARY KEY,
    body       TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS quizzes (
    module_id  INTEGER PRIMARY KEY,
    question   TEXT NOT NULL,
    expected   TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def touch_user(self, user_id: int, name: str, username: str | None) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO users (id, name, username, created_at, last_seen) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
                "username=excluded.username, last_seen=excluded.last_seen",
                (user_id, name, username, now, now),
            )

    def set_progress(
        self, user_id: int, module_id: int, status: str, score: int | None = None
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            if score is None:
                conn.execute(
                    "INSERT INTO progress (user_id, module_id, status, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(user_id, module_id) DO UPDATE SET "
                    "status=excluded.status, updated_at=excluded.updated_at",
                    (user_id, module_id, status, now),
                )
            else:
                conn.execute(
                    "INSERT INTO progress (user_id, module_id, status, score, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(user_id, module_id) DO UPDATE SET "
                    "status=excluded.status, score=excluded.score, "
                    "updated_at=excluded.updated_at",
                    (user_id, module_id, status, score, now),
                )

    def user_progress(self, user_id: int) -> dict[int, dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT module_id, status, score FROM progress WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return {
            r["module_id"]: {"status": r["status"], "score": r["score"]} for r in rows
        }

    def stats(self) -> dict:
        with self._conn() as conn:
            users = conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]
            done = conn.execute(
                "SELECT count(*) AS n FROM progress WHERE status = 'done'"
            ).fetchone()["n"]
        return {"users": users, "completed": done}

    # --- Dars matnlari keshi: bir marta yaratiladi, keyin kvota sarflanmaydi ---

    def get_lesson(self, module_id: int) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT body FROM lessons WHERE module_id = ?", (module_id,)
            ).fetchone()
        return row["body"] if row else None

    def save_lesson(self, module_id: int, body: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO lessons (module_id, body, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(module_id) DO UPDATE SET body=excluded.body",
                (module_id, body, time.time()),
            )

    def get_quiz(self, module_id: int) -> tuple[str, str] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT question, expected FROM quizzes WHERE module_id = ?",
                (module_id,),
            ).fetchone()
        return (row["question"], row["expected"]) if row else None

    def save_quiz(self, module_id: int, question: str, expected: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO quizzes (module_id, question, expected, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(module_id) DO UPDATE SET "
                "question=excluded.question, expected=excluded.expected",
                (module_id, question, expected, time.time()),
            )

    def cache_stats(self) -> dict:
        with self._conn() as conn:
            lessons = conn.execute("SELECT count(*) AS n FROM lessons").fetchone()["n"]
            quizzes = conn.execute("SELECT count(*) AS n FROM quizzes").fetchone()["n"]
        return {"lessons": lessons, "quizzes": quizzes}

# ==================== rag ====================
_WORD = re.compile(r"[\w\u0400-\u04FF\u02BB\u02BC\u2018\u2019']+", re.UNICODE)

RAG_SCHEMA = """
CREATE TABLE IF NOT EXISTS modules (
    id      INTEGER PRIMARY KEY,
    title   TEXT NOT NULL,
    source  TEXT NOT NULL,
    ord     INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    body,
    module UNINDEXED,
    source UNINDEXED,
    tokenize = "unicode61 remove_diacritics 2"
);
"""


_QUIZ_RE = re.compile(r"(^|\s)[A-DА-ГӘ]\s*\)", re.MULTILINE)
_DAY_RE = re.compile(r"^\d+\s*[-–]\s*кун", re.IGNORECASE | re.MULTILINE)


def _tokens(query: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(query) if len(t) > 1]


class RAG:
    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        with self._conn() as conn:
            conn.executescript(RAG_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------- yozish ----------
    def reset(self) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM modules")

    def add_chunks(self, module_id: int, title: str, source: str, chunks: list[str]) -> None:
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO chunks (body, module, source) VALUES (?, ?, ?)",
                [(c, module_id, source) for c in chunks],
            )
            conn.execute(
                "UPDATE modules SET title = ? WHERE id = ?", (title, module_id)
            )

    def add_module(self, title: str, source: str, ord_: int = 0) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO modules (title, source, ord) VALUES (?, ?, ?)",
                (title, source, ord_),
            )
            return int(cur.lastrowid)

    def has_data(self) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()
            return bool(row and row["n"])

    # ---------- o'qish ----------
    def modules(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, title, source FROM modules ORDER BY ord, id"
            ).fetchall()
            out = []
            for r in rows:
                n = conn.execute(
                    "SELECT count(*) AS n FROM chunks WHERE module = ?", (r["id"],)
                ).fetchone()["n"]
                out.append(
                    {"id": r["id"], "title": r["title"], "source": r["source"], "chunks": n}
                )
            return out

    def module_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT count(*) AS n FROM modules").fetchone()["n"]

    def module_chunks(self, module_id: int, limit: int = 12) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT body FROM chunks WHERE module = ? LIMIT ?",
                (module_id, limit),
            ).fetchall()
            return [r["body"] for r in rows]

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Avval barcha so'zlar mos kelishini (AND), keyin istalganini (OR),
        oxirida oddiy LIKE qidiradi. Soʻrov lotin boʻlsa kirillga ham oʻgiriladi."""
        toks = _tokens(query)
        if not toks:
            return self._like(query, limit)

        cyr = _tokens(to_cyrillic(query))
        variants = [toks] if not cyr or cyr == toks else [toks, cyr]
        all_toks = list(dict.fromkeys(toks + cyr))

        for variant in variants:
            for joiner in (" ", " OR "):
                match = joiner.join(f'"{t}"*' for t in variant)
                rows = self._fts(match, limit * 6)
                if rows:
                    return self._rerank(rows, all_toks, limit)
        for text in dict.fromkeys([query, to_cyrillic(query)]):
            rows = self._like(text, limit)
            if rows:
                return rows
        return []

    def _rerank(self, rows: list[dict], toks: list[str], limit: int) -> list[dict]:
        """Tushuntirish matnini test savollaridan ustun qo'yadi."""
        scored: list[tuple[float, dict]] = []
        for row in rows:
            body = row["body"]
            low = body.lower()
            score = sum(low.count(t) for t in toks) * 2.0
            if "тушунтириш" in low:
                score += 3
            if _DAY_RE.search(body):
                score += 2
            if _QUIZ_RE.search(body):
                score -= 4
            score -= len(body) / 4000
            scored.append((score, row))

        scored.sort(key=lambda item: -item[0])
        out: list[dict] = []
        per_source: dict[str, int] = {}
        for _, row in scored:
            src = row["source"]
            if per_source.get(src, 0) >= 2:
                continue
            per_source[src] = per_source.get(src, 0) + 1
            out.append(row)
            if len(out) >= limit:
                break
        return out

    def _fts(self, match: str, limit: int) -> list[dict]:
        sql = (
            "SELECT body, module, source, bm25(chunks) AS score "
            "FROM chunks WHERE chunks MATCH ? ORDER BY score LIMIT ?"
        )
        try:
            with self._conn() as conn:
                rows = conn.execute(sql, (match, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            {"body": r["body"], "module": r["module"], "source": r["source"]}
            for r in rows
        ]

    def _like(self, query: str, limit: int) -> list[dict]:
        needle = query.strip()
        if not needle:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT body, module, source, "
                "(length(body) - length(replace(body, ?, ''))) AS hits "
                "FROM chunks WHERE body LIKE ? ORDER BY hits DESC LIMIT ?",
                (needle, f"%{needle}%", limit),
            ).fetchall()
        return [
            {"body": r["body"], "module": r["module"], "source": r["source"]}
            for r in rows
        ]

# ==================== teacher ====================
PERSONA = """Sen — "Ustoz" ismli tajribali oʻqituvchisan. Sen kurs materiali asosida dars oʻtasan.

TIL: faqat oʻzbek tilida, LOTIN yozuvida yoz. Kurs materiali kirill yozuvida boʻlsa,
uni lotin yozuviga oʻgirib tushuntir (акция → aksiya, дивиденд → dividend).
Kirill harflarini umuman ishlatma — na sarlavhada, na matnda.
Imlo: aksiya, aksiyador, obligatsiya, dividend, broker, tiker, emitent
("aktsiya", "divident" kabi xato yozuvlardan saqlan).
Faqat rasmiy nomlar, tikerlar va kompaniya nomlarini asl holida qoldir.

QOIDALAR:
1. Javobni FAQAT berilgan KONTEKST asosida ber. Kontekstda yoʻq boʻlsa, aynan shunday deb ayt:
   "Bu savol kurs materialida yoritilmagan. Keling, kurs doirasidagi mavzuga qaytamiz."
   Hech qachon oʻzingdan maʼlumot toʻqima.
2. Oʻzbek tilida, sodda va tushunarli yoz. Kerak boʻlsa hayotiy misol keltir.
3. Javob 3–6 jumladan oshmasin (dars — alohida koʻrsatma bilan — bundan mustasno). Uzun mavzuni boʻlaklarga boʻlib tushuntir.
4. Har javob oxirida talabaga BITTA tekshiruv savoli ber (uni "Savol:" deb boshlab yoz).
5. Talaba xato qilsa — avval xatoni muloyim koʻrsat, keyin toʻgʻri yoʻlni koʻrsat. Hech qachon "notoʻgʻri" deb qoʻyib yuborma, sababini tushuntir.
6. Emoji juda kam ishlat (0–2 ta).
7. Manba qatorini ("Manba: ...") oʻzing yozma — tizim uni avtomatik qoʻshadi."""

LESSON = """Sen tajribali oʻqituvchisan. Darsni materialdan koʻchirib bermaysan — talabaga jonli tushuntirasan.

MODUL: {title}

MATERIAL:
---
{context}
---

Shu mavzuni darsda oʻqituvchi aytib berayotgandek tushuntir. Aynan shu tuzilishda yoz:

Bugungi mavzu: <1 jumla — nima va nima uchun muhim>

Oddiy qilib aytganda:
<2–3 jumla, hayotiy oʻxshatish bilan. Masalan: "... xuddi ... kabi">

Batafsil:
1) <tushuncha> — <izoh> + <kichik misol>
2) <tushuncha> — <izoh> + <kichik misol>
3) <tushuncha> — <izoh> + <kichik misol>

Hayotiy misol:
<aniq vaziyat: kim nima qiladi, nima boʻladi, natija qanday>

Adashtirmang:
- <keng tarqalgan xato yoki tushunmovchilik>
- <yana bittasi>

Xulosa:
<2–3 jumla — asosiy fikrni oʻz soʻzing bilan>

Esda saqlang:
- <qisqa xulosa>
- <qisqa xulosa>
- <qisqa xulosa>

Talablar:
- 900–1900 belgi atrofida yoz; ortiqcha choʻzma.
- Materialdagi jumlalarni soʻzma-soʻz koʻchirma — oʻz soʻzing bilan qayta ayt.
- Yangi fakt, raqam, kompaniya yoki sana oʻylab topma — faqat materialdagidan foydalan.
- Savol berma — savolni tizim oʻzi qoʻshadi.
- Faqat dars matnini yoz, kirish soʻzi va izohsiz."""

ANSWER = """Talaba savoliga kurs materiali asosida javob ber.

SAVOL: {question}

KONTEKST (kurs materialidan):
---
{context}
---

Agar kontekst savolga javob berishga yetarli boʻlmasa — buni ochiq ayt va kurs doirasidagi eng yaqin mavzuni taklif qil."""

QUIZ = """Quyidagi kurs materiali asosida BITTA test savoli tuz.

MODUL: {title}

MATERIAL:
---
{context}
---

Talab: savol aniq va bir xil javobli boʻlsin. Javobni ham ber.

Aynan shu formatda yoz, boshqa narsa qoʻshma:
SAVOL: <savol matni>
JAVOB: <toʻgʻri javob, 1–2 jumla>"""

GRADE = """Sen oʻqituvchisan. Talabaning javobini baholaysan.
SAVOL: {question}
KUTILGAN JAVOB: {expected}
TALABA JAVOBI: {answer}

1. Javob toʻgʻrimi — "Toʻgʻri", "Qisman toʻgʻri" yoki "Notoʻgʻri" deb boshlangʻich baho ber.
2. 2–3 jumlada izohla: nima toʻgʻri, nima yetishmayapti.
3. Toʻgʻri javobni qisqa tushuntir.
4. Oxirida ragʻbatlantiruvchi 1 jumla yoz."""


def _context(chunks: list[str], limit: int = 6000) -> str:
    out: list[str] = []
    used = 0
    for chunk in chunks:
        if used + len(chunk) > limit:
            break
        out.append(chunk)
        used += len(chunk)
    return "\n\n".join(out) if out else "(boʻsh)"


def lesson_messages(title: str, chunks: list[str]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": PERSONA},
        {"role": "user", "content": LESSON.format(title=title, context=_context(chunks))},
    ]


def answer_messages(question: str, chunks: list[str]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": PERSONA},
        {"role": "user", "content": ANSWER.format(question=question, context=_context(chunks))},
    ]


def quiz_messages(title: str, chunks: list[str]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": PERSONA},
        {"role": "user", "content": QUIZ.format(title=title, context=_context(chunks))},
    ]


def grade_messages(question: str, expected: str, answer: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": PERSONA},
        {
            "role": "user",
            "content": GRADE.format(question=question, expected=expected, answer=answer),
        },
    ]


CHECK = """Sen oʻqituvchisan. Darsdan soʻng talaba mavzu savoliga javob yozdi.

DARS MATNI:
{lesson}

SAVOL: {question}
TALABA JAVOBI: {answer}

Javobni dars mazmuniga qarab baholang. Bu OCHIQ savol — soʻzma-soʻz mos kelishi shart emas:
agar talaba asosiy maʼnoni toʻgʻri aytgan boʻlsa, "Toʻgʻri" deb baholang.
Agar mazmunning bir qismi toʻgʻri boʻlsa — "Qisman toʻgʻri".
Faqat mavzuga aloqasi boʻlmagan yoki xato javobda — "Notoʻgʻri".

Javobni shunday yozing:
1. Birinchi qatorda: "Toʻgʻri", "Qisman toʻgʻri" yoki "Notoʻgʻri".
2. 2–3 jumlada izoh: nima toʻgʻri, nimani qoʻshish mumkin.
3. "Toʻgʻri javob: <qisqa namunaviy javob>"
4. "Keyingi dars: /dars {next}"

6 jumladan oshmang."""


def check_messages(
    lesson: str, question: str, answer: str, next_number: int = 2
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": PERSONA},
        {
            "role": "user",
            "content": CHECK.format(
                lesson=lesson[:3000],
                question=question,
                answer=answer,
                next=next_number,
            ),
        },
    ]


FALLBACK = """Talaba kurs mavzusidan tashqari yoki noaniq xabar yozdi:

"{text}"

Vazifang:
1. Agar xabar kurs mavzusiga oid savol boʻlsa — 2–3 jumlada qisqa javob ber.
2. Agar talaba tushunmaganini yoki norozi ekanini bildirsa — muloyim tushuntir,
   qaysi qismini qayta aytib berishni soʻra.
3. Aks holda — qisqa yoʻnaltir: /kurs (mavzular), /dars (keyingi dars).
Oʻzingni takrorlama, 4 jumladan oshma."""


def fallback_messages(text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": PERSONA},
        {"role": "user", "content": FALLBACK.format(text=text[:500])},
    ]


def parse_quiz(text: str) -> tuple[str, str]:
    """LLM javobidan SAVOL/JAVOB ni ajratib oladi."""
    question, answer = "", ""
    for line in text.splitlines():
        clean = line.strip()
        upper = clean.upper()
        if upper.startswith("SAVOL:"):
            question = clean[6:].strip()
        elif upper.startswith("JAVOB:"):
            answer = clean[6:].strip()
    if not question:
        question = text.strip()[:500]
    if not answer:
        answer = "Kurs materialiga qarang."
    return question, answer

# ==================== common ====================
MAX_LEN = 3800

# "dars 1", "дарс 1", "test 2" kabi buyruqlarni oddiy matndan ham tanish
TEXT_CMD = re.compile(
    r"^/?(dars|дарс|lesson|test|тест|kurs|курс|progress|прогресс)\s*(\d+)?$",
    re.IGNORECASE,
)

_TOPIC_RE = re.compile(r"^\d+\s*[-–]\s*кун\s*:\s*(.+)$", re.IGNORECASE)


def topic_question(title: str) -> str:
    """Savolni dars nomidan oladi — model oʻylab topmaydi."""
    match = _TOPIC_RE.match(title.strip())
    topic = (match.group(1) if match else title).strip().rstrip("?.! ")
    topic = to_latin(topic).strip()
    if not topic:
        return "Bugungi mavzuni qisqa tushuntirib bering."
    return f"{topic}?"


def next_module_number(modules: list[dict], progress: dict[int, dict]) -> int:
    """Tugallanmagan birinchi darsni qaytaradi."""
    for idx, module in enumerate(modules, start=1):
        if progress.get(module["id"], {}).get("status") != "done":
            return idx
    return 1


def split_text(text: str, size: int = MAX_LEN) -> list[str]:
    text = (text or "").strip()
    if not text:
        return ["(boʻsh javob)"]
    parts: list[str] = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = text.rfind(" ", 0, size)
        if cut < size // 2:
            cut = size
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def course_map(rag: RAG, user_id: int, db: DB) -> str:
    modules = rag.modules()
    if not modules:
        return (
            "📚 Kurs hali yuklanmagan.\n\n"
            "Administrator kurs materialini (PDF/DOCX/PPTX) yuklashi kerak."
        )
    progress = db.user_progress(user_id)
    lines = ["📚 KURS XARITASI", ""]
    for idx, item in enumerate(modules, start=1):
        st = progress.get(item["id"], {}).get("status", "new")
        mark = {"done": "✅", "in_progress": "🟡"}.get(st, "⬜")
        lines.append(f"{mark} {idx}. {to_latin(item['title'])}")
    lines += [
        "",
        "Buyruqlar:",
        "/dars 1 — darsni boshlash",
        "/test 1 — test topshirish",
        "/progress — natijalarim",
        "",
        "Yoki shunchaki savolingizni yozing — javob beraman.",
    ]
    return "\n".join(lines)

# ==================== llm ====================
llm_log = logging.getLogger("llm")


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0))
        # model -> vaqt (shu vaqtgacha urinmaymiz)
        self._cooldown: dict[str, float] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.6,
        max_tokens: int = 900,
    ) -> str:
        errors: list[str] = []
        for provider in self.cfg.llm_chain:
            for attempt in range(2):
                try:
                    text = await self._call(provider, messages, temperature, max_tokens)
                    if text:
                        return text
                    raise LLMError("bo'sh javob")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{provider}: {exc}")
                    llm_log.warning("LLM %s urinish %d xato: %s", provider, attempt + 1, exc)
                    if attempt == 0:
                        await asyncio.sleep(1.5)
        raise LLMError("; ".join(errors) or "LLM sozlanmagan (LLM env bo'sh)")

    async def _call(
        self,
        provider: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        if provider == "gemini":
            return await self._gemini(messages, temperature, max_tokens)
        if provider == "openai":
            return await self._openai_like(
                self.cfg.openai_base, self.cfg.openai_api_key,
                self.cfg.openai_model, messages, temperature, max_tokens,
            )
        if provider == "groq":
            return await self._openai_like(
                self.cfg.groq_base, self.cfg.groq_api_key,
                self.cfg.groq_model, messages, temperature, max_tokens,
            )
        if provider == "ollama":
            return await self._ollama(messages, temperature, max_tokens)
        raise LLMError(f"noma'lum provayder: {provider}")

    async def _gemini(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> str:
        if not self.cfg.gemini_api_key:
            raise LLMError("GEMINI_API_KEY yo'q")

        errors: list[str] = []
        now = time.time()
        for model in self.cfg.gemini_models:
            wait_left = self._cooldown.get(model, 0) - now
            if wait_left > 0:
                errors.append(f"{model}: limit (yana {wait_left / 60:.0f} daq)")
                continue
            try:
                return await self._gemini_model(
                    model, messages, temperature, max_tokens
                )
            except LLMError as exc:
                errors.append(f"{model}: {str(exc)[:140]}")
                self._note_error(model, str(exc))
        raise LLMError("; ".join(errors) or "gemini: model ro'yxati bo'sh")

    def _note_error(self, model: str, message: str) -> None:
        """Limit/xato sababiga qarab modelni vaqtincha chetga chiqaradi."""
        now = time.time()
        if "HTTP 429" in message:
            match = re.search(r"retryDelay\\?\"?\s*:\s*\\?\"(\d+)", message)
            delay = int(match.group(1)) if match else 3600
            delay = min(max(delay, 60), 24 * 3600)
            self._cooldown[model] = now + delay
            llm_log.warning(
                "Model %s limiti tugadi — %.1f soatga chetga chiqarildi",
                model,
                delay / 3600,
            )
        elif "HTTP 404" in message:
            self._cooldown[model] = now + 24 * 3600
            llm_log.warning("Model %s mavjud emas", model)
        elif "HTTP 503" in message:
            self._cooldown[model] = now + 10 * 60
            llm_log.warning("Model %s band — 10 daqiqaga chetga chiqarildi", model)

    async def _gemini_model(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system").strip()
        contents = [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
            if m["role"] != "system"
        ]
        gen_cfg: dict = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        # "Oʻylash" tokenlari javob byudjetini yeb qoʻymasligi uchun.
        # Lite modellar bu parametrni qabul qilmaydi.
        use_thinking = self.cfg.gemini_thinking >= 0 and "lite" not in model
        if use_thinking:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": self.cfg.gemini_thinking}

        body: dict = {"contents": contents, "generationConfig": gen_cfg}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        resp = await self._client.post(
            url, params={"key": self.cfg.gemini_api_key}, json=body
        )

        if resp.status_code == 400 and use_thinking:
            # Ba'zi modellar thinkingConfig'ni bilmaydi — usiz qayta urinamiz
            body["generationConfig"].pop("thinkingConfig", None)
            resp = await self._client.post(
                url, params={"key": self.cfg.gemini_api_key}, json=body
            )

        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:400]}")

        data = resp.json()
        try:
            candidate = data["candidates"][0]
            parts = candidate["content"]["parts"]
            text_out = "".join(p.get("text", "") for p in parts).strip()
        except (KeyError, IndexError) as exc:
            raise LLMError(f"kutilmagan javob: {str(data)[:200]}") from exc

        finish = candidate.get("finishReason")
        usage = data.get("usageMetadata", {})
        if finish == "MAX_TOKENS":
            llm_log.warning(
                "Javob kesildi (MAX_TOKENS): model=%s, max=%d, javob=%s, fikr=%s",
                model,
                max_tokens,
                usage.get("candidatesTokenCount"),
                usage.get("thoughtsTokenCount"),
            )
            # Byudjetni oshirib qayta urinamiz
            body["generationConfig"]["maxOutputTokens"] = max_tokens * 3
            retry = await self._client.post(
                url, params={"key": self.cfg.gemini_api_key}, json=body
            )
            if retry.status_code == 200:
                data = retry.json()
                try:
                    cand = data["candidates"][0]
                    text_out = "".join(
                        p.get("text", "") for p in cand["content"]["parts"]
                    ).strip()
                except (KeyError, IndexError):
                    pass
        return text_out

    async def _openai_like(
        self,
        base: str,
        key: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not key:
            raise LLMError("API kalit yo'q")
        resp = await self._client.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()["choices"][0]["message"]["content"].strip()

    async def _ollama(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> str:
        resp = await self._client.post(
            f"{self.cfg.ollama_url.rstrip('/')}/api/chat",
            json={
                "model": self.cfg.ollama_model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
        )
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()["message"]["content"].strip()

# ==================== mini ====================
log = logging.getLogger("mini")

START_TEXT = (
    "Assalomu alaykum! 👋\n\n"
    "Men — Ustoz, sizning kurs oʻqituvchingizman.\n"
    "Mavzularni tushuntiraman, savollaringizga javob beraman va "
    "bilimingizni test bilan tekshiraman.\n\n"
)

HELP_TEXT = (
    "🤖 QANDAY FOYDALANAMAN\n\n"
    "/kurs — modullar roʻyxati\n"
    "/dars 1 — 1-modul darsini boshlash\n"
    "/test 1 — 1-moduldan test\n"
    "/progress — natijalarim\n\n"
    "Savolingizni oddiy matn bilan yozsangiz ham boʻladi:\n"
    "masalan: \"Bu mavzuni soddaroq tushuntir\""
)

QUOTA_TEXT = (
    "⚠️ Bepul kunlik limit tugadi (kuniga 20 soʻrov/model).\n\n"
    "Tayyor darslar keshlangan — ular kvotasiz ochiladi. "
    "Limit ertaga yangilanadi."
)


class MiniBot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.rag = RAG(cfg.db_path)
        self.db = DB(cfg.db_path)
        self.llm = LLM(cfg)
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0))
        # chat_id -> {"kind": "lesson"|"quiz", ...}
        self.states: dict[int, dict] = {}
        self.base = f"https://api.telegram.org/bot{cfg.bot_token}/"

    # ---------------- Telegram API ----------------
    async def api(self, method: str, **params):
        resp = await self.client.post(self.base + method, json=params)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"{method}: {data.get('description')}")
        return data["result"]

    async def send(self, chat_id: int, text: str) -> None:
        for part in split_text(text):
            await self.api("sendMessage", chat_id=chat_id, text=part)

    async def typing(self, chat_id: int) -> None:
        try:
            await self.api("sendChatAction", chat_id=chat_id, action="typing")
        except Exception:  # noqa: BLE001
            pass

    async def error(self, chat_id: int, exc: Exception) -> None:
        log.exception("Xato: %s", exc)
        text = str(exc).lower()
        if "429" in text or "limit" in text or "quota" in text:
            await self.send(chat_id, QUOTA_TEXT)
        else:
            await self.send(
                chat_id,
                "⚠️ Hozir javob bera olmadim (model band).\n"
                "Birozdan keyin qayta urinib koʻring.",
            )

    # ---------------- dars / test ----------------
    async def send_lesson(self, chat_id: int, uid: int, number: int | None) -> None:
        modules = self.rag.modules()
        if not modules:
            await self.send(chat_id, "Kurs yuklanmagan.")
            return
        if number is None:
            number = next_module_number(modules, self.db.user_progress(uid))
            await self.send(chat_id, f"Davom etamiz: {number}-dars")
        if not 1 <= number <= len(modules):
            await self.send(
                chat_id, f"{number}-modul topilmadi. /kurs bilan roʻyxatni koʻring."
            )
            return

        module = modules[number - 1]
        question = topic_question(module["title"])
        body = self.db.get_lesson(module["id"])

        if body is None:
            await self.typing(chat_id)
            await self.send(
                chat_id, f"📖 {to_latin(module['title'])}\n\nTayyorlanmoqda..."
            )
            try:
                text = await self.llm.chat(
                    lesson_messages(
                        to_latin(module["title"]),
                        self.rag.module_chunks(module["id"]),
                    ),
                    temperature=0.5,
                    max_tokens=1100,
                )
                text = to_latin(text)
            except LLMError as exc:
                await self.error(chat_id, exc)
                return
            body = re.sub(r"(?ms)^\s*\**Savol\**\s*:.*\Z", "", text).strip()
            self.db.save_lesson(module["id"], body)

        text = f"{body}\n\nSavol: {question}"
        self.db.set_progress(uid, module["id"], "in_progress")
        await self.send(chat_id, f"{text}\n\nManba: {to_latin(module['title'])}")

        self.states[chat_id] = {
            "kind": "lesson",
            "module_id": module["id"],
            "lesson": text[:3000],
            "question": question,
            "next": number + 1,
        }
        await self.send(chat_id, "Javobingizni yozing — tekshiraman.")

    async def send_quiz(self, chat_id: int, uid: int, number: int | None) -> None:
        modules = self.rag.modules()
        if not modules:
            await self.send(chat_id, "Kurs yuklanmagan.")
            return
        if number is None:
            number = next_module_number(modules, self.db.user_progress(uid))
        if not 1 <= number <= len(modules):
            await self.send(chat_id, "Bunday modul yoʻq. /kurs ni koʻring.")
            return

        module = modules[number - 1]
        cached = self.db.get_quiz(module["id"])
        if cached:
            question, expected = cached
        else:
            await self.typing(chat_id)
            try:
                raw = await self.llm.chat(
                    quiz_messages(
                        to_latin(module["title"]),
                        self.rag.module_chunks(module["id"]),
                    ),
                    temperature=0.5,
                    max_tokens=500,
                )
                raw = to_latin(raw)
            except LLMError as exc:
                await self.error(chat_id, exc)
                return
            question, expected = parse_quiz(raw)
            self.db.save_quiz(module["id"], question, expected)

        self.db.set_progress(uid, module["id"], "in_progress")
        self.states[chat_id] = {
            "kind": "quiz",
            "module_id": module["id"],
            "question": question,
            "expected": expected,
        }
        await self.send(
            chat_id,
            f"📝 TEST — {to_latin(module['title'])}\n\n{question}\n\n"
            "Javobingizni yozing.",
        )

    # ---------------- progress / xarita ----------------
    async def send_progress(self, chat_id: int, uid: int) -> None:
        modules = self.rag.modules()
        if not modules:
            await self.send(chat_id, "Kurs yuklanmagan.")
            return
        progress = self.db.user_progress(uid)
        done = sum(1 for v in progress.values() if v["status"] == "done")
        lines = ["📊 NATIJALARIM", ""]
        for idx, item in enumerate(modules, start=1):
            info = progress.get(item["id"], {})
            st = info.get("status", "new")
            mark = {"done": "✅", "in_progress": "🟡"}.get(st, "⬜")
            score = f" — {info['score']}%" if info.get("score") else ""
            lines.append(f"{mark} {idx}. {to_latin(item['title'])}{score}")
        lines += ["", f"Tugallangan: {done}/{len(modules)}"]
        await self.send(chat_id, "\n".join(lines))

    # ---------------- asosiy mantiq ----------------
    async def handle(self, chat_id: int, uid: int, text: str) -> None:
        # 1) buyruqlar
        if text.startswith("/"):
            head, _, rest = text.partition(" ")
            cmd = head[1:].split("@")[0].lower()
            args = rest.strip()

            if cmd == "start":
                await self.send(
                    chat_id,
                    START_TEXT + course_map(self.rag, uid, self.db),
                )
                return
            if cmd in {"kurs", "course"}:
                await self.send(chat_id, course_map(self.rag, uid, self.db))
                return
            if cmd == "dars":
                await self.send_lesson(
                    chat_id, uid, int(args) if args.isdigit() else None
                )
                return
            if cmd == "test":
                await self.send_quiz(
                    chat_id, uid, int(args) if args.isdigit() else None
                )
                return
            if cmd == "progress":
                await self.send_progress(chat_id, uid)
                return
            if cmd in {"yordam", "help"}:
                await self.send(chat_id, HELP_TEXT)
                return

        # 2) "dars 2", "test 3" kabi matn koʻrinishidagi buyruqlar
        match = TEXT_CMD.match(text)
        if match:
            kind = match.group(1).lower()
            number = int(match.group(2)) if match.group(2) else None
            if kind in {"dars", "дарс", "lesson"}:
                await self.send_lesson(chat_id, uid, number)
                return
            if kind in {"test", "тест"}:
                await self.send_quiz(chat_id, uid, number)
                return
            if kind in {"progress", "прогресс"}:
                await self.send_progress(chat_id, uid)
                return
            await self.send(chat_id, course_map(self.rag, uid, self.db))
            return

        # 3) javobni baholash
        state = self.states.pop(chat_id, None)
        if state and state["kind"] == "quiz":
            await self.typing(chat_id)
            try:
                verdict = await self.llm.chat(
                    grade_messages(
                        state["question"], state["expected"], text
                    ),
                    temperature=0.3,
                    max_tokens=600,
                )
                verdict = to_latin(verdict)
            except LLMError as exc:
                await self.error(chat_id, exc)
                return
            self.db.set_progress(uid, state["module_id"], "done", score=80)
            if "toʻgʻri javob" not in verdict.lower():
                verdict = f"{verdict}\n\nToʻgʻri javob: {state['expected']}"
            await self.send(chat_id, verdict)
            return

        if state and state["kind"] == "lesson":
            await self.typing(chat_id)
            try:
                verdict = await self.llm.chat(
                    check_messages(
                        state.get("lesson", ""),
                        state.get("question", ""),
                        text,
                        state.get("next", 2),
                    ),
                    temperature=0.3,
                    max_tokens=900,
                )
                verdict = to_latin(verdict)
            except LLMError as exc:
                await self.error(chat_id, exc)
                return
            self.db.set_progress(uid, state["module_id"], "done", score=85)
            await self.send(chat_id, verdict)
            return

        # 4) erkin savol — kurs materialidan qidiramiz
        await self.typing(chat_id)
        chunks = self.rag.search(text, limit=5)
        if not chunks:
            try:
                answer = await self.llm.chat(
                    fallback_messages(text), temperature=0.5, max_tokens=400
                )
            except LLMError as exc:
                await self.error(chat_id, exc)
                return
            await self.send(chat_id, to_latin(answer))
            return

        context = [c["body"] for c in chunks]
        try:
            answer = await self.llm.chat(
                answer_messages(text, context),
                temperature=0.6,
                max_tokens=900,
            )
            answer = to_latin(answer)
        except LLMError as exc:
            await self.error(chat_id, exc)
            return

        module_id = chunks[0].get("module")
        source = ""
        if module_id:
            source = next(
                (
                    to_latin(m["title"])
                    for m in self.rag.modules()
                    if m["id"] == module_id
                ),
                "",
            )
        if source:
            answer = f"{answer}\n\nManba: {source}"
        await self.send(chat_id, answer)

    # ---------------- polling ----------------
    async def on_update(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        chat_id = message["chat"]["id"]
        frm = message.get("from") or {}
        uid = frm.get("id", chat_id)
        name = " ".join(
            part for part in [frm.get("first_name"), frm.get("last_name")] if part
        ) or "Talaba"
        self.db.touch_user(uid, name, frm.get("username"))
        text = (message.get("text") or "").strip()
        if not text:
            return
        log.info("Xabar: %s: %s", uid, text[:120].replace("\n", " "))
        await self.handle(chat_id, uid, text)

    async def poll(self) -> None:
        while True:
            try:
                me = await self.api("getMe")
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("getMe xatosi: %s — 10 s dan keyin qayta", exc)
                await asyncio.sleep(10)
        log.info(
            "Bot ishga tushdi | @%s | modullar: %d",
            me.get("username"),
            len(self.rag.modules()),
        )
        try:
            await self.api(
                "setMyCommands",
                commands=[
                    {"command": "kurs", "description": "Kurs xaritasi"},
                    {"command": "dars", "description": "Keyingi dars"},
                    {"command": "test", "description": "Test topshirish"},
                    {"command": "progress", "description": "Natijalarim"},
                    {"command": "yordam", "description": "Qoʻllanma"},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("setMyCommands: %s", exc)

        offset = 0
        while True:
            try:
                updates = await self.api(
                    "getUpdates", offset=offset, timeout=30, allowed_updates=["message"]
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("getUpdates xatosi: %s", exc)
                await asyncio.sleep(3)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    await self.on_update(update)
                except Exception:  # noqa: BLE001
                    log.exception("Update xatosi")


async def bot_main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load()
    if not cfg.bot_token:
        raise SystemExit("BOT_TOKEN yoʻq — .env faylini toʻldiring")
    bot = MiniBot(cfg)
    try:
        await bot.poll()
    finally:
        await bot.llm.close()
        await bot.client.aclose()


# ==================== health server + ishga tushirish ====================
class Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # noqa: D401
        pass


def serve_health() -> None:
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), Health)
    logging.getLogger("deploy").info("Health server: :%d", port)
    server.serve_forever()


def self_ping() -> None:
    """Render bepul rejasi 15 daqiqada uxlaydi — har 5 daqiqada oʻziga soʻrov yuboramiz."""
    base = (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL")
            or "https://ustoz-bot.onrender.com").rstrip("/")
    url = base + "/health"
    kl = logging.getLogger("keepalive")
    while True:
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                kl.info("keepalive: %s", resp.status)
        except Exception as exc:  # noqa: BLE001
            kl.warning("keepalive xatosi: %s", exc)
        time.sleep(300)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    threading.Thread(target=serve_health, daemon=True).start()
    threading.Thread(target=self_ping, daemon=True).start()
    log = logging.getLogger("deploy")
    while True:
        try:
            asyncio.run(bot_main())
        except Exception:  # noqa: BLE001
            log.exception("Bot toʻxtadi — 10 soniyadan keyin qayta urinamiz")
            time.sleep(10)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
