#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote
from urllib import error, request

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL_DEFAULT = "gpt-4o-mini"
OPENAI_MODEL_ALLOWLIST = {"gpt-4o-mini", "gpt-4o"}
RATE_WINDOW_SECONDS = 60
RATE_MAX_REQUESTS = 20
MAX_CHAT_BODY_BYTES = 8_000_000
MAX_ADMIN_BODY_BYTES = 30_000_000
MAX_ADMIN_UPLOAD_BYTES = 40_000_000
MAX_FEEDBACK_BODY_BYTES = 60_000
DEFAULT_PORT = 8000
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MATERIALS_DIR = BASE_DIR / "materials"
DEFAULT_FEEDBACK_PATH = BASE_DIR / "feedback.jsonl"
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".csv"}
CHUNK_SIZE = 1400
CHUNK_OVERLAP = 220
_max_answer_chars = int(os.environ.get("MAX_ANSWER_CHARS", "0"))
_default_system_prompt = os.environ.get(
    "DEFAULT_SYSTEM_PROMPT",
    "Answer primarily based on the provided class materials. If the materials are incomplete, continue with a concise, useful answer using reliable external knowledge. Do not use template disclaimers like 'the provided materials do not include information about ...'.",
).strip()
_user_page_password = os.environ.get("USER_PAGE_PASSWORD", "12345678!").strip()

_request_log = defaultdict(deque)
_kb_chunks = []
_kb_files = []
_materials_dir = DEFAULT_MATERIALS_DIR
_feedback_path = DEFAULT_FEEDBACK_PATH
_feedback_entries = []


def _rate_limit_ok(ip: str) -> bool:
    now = time.time()
    q = _request_log[ip]
    while q and (now - q[0]) > RATE_WINDOW_SECONDS:
        q.popleft()
    if len(q) >= RATE_MAX_REQUESTS:
        return False
    q.append(now)
    return True


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", text.lower()) if t]

def _is_unclear_user_query(text: str) -> bool:
    s = text.strip()
    if len(s) < 2:
        return True
    lowered = s.lower()
    vague = {
        "?",
        "??",
        "뭐",
        "뭔데",
        "뭐야",
        "왜",
        "어떻게",
        "help",
    }
    return lowered in vague

def _is_unclear_response(text: str) -> bool:
    lowered = text.lower()
    markers = [
        "질문을 더 구체",
        "질문을 명확",
        "다시 설명",
        "clarify",
        "more details",
        "not clear",
        "could you specify",
    ]
    return any(m in lowered for m in markers)

def _remove_material_disclaimer(text: str) -> str:
    # Remove common template disclaimers like:
    # "제공된 자료에는 ... 정보가 포함되어 있지 않습니다."
    patterns = [
        r"제공된\s*자료[에는엔].{0,160}?포함되어\s*있지\s*않습니다\.?",
        r"자료[에는엔].{0,160}?없습니다\.?",
        r"제공된\s*자료[에는엔].{0,160}?확인할\s*수\s*없습니다\.?",
    ]
    cleaned = text
    for p in patterns:
        cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not cleaned:
        return "현재 자료와 일반 지식을 바탕으로 핵심만 간단히 답변드릴게요."
    return cleaned

def _sanitize_history(history) -> list[dict]:
    if not isinstance(history, list):
        return []
    cleaned = []
    for item in history[-20:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role not in {"user", "assistant"}:
            continue
        if not content:
            continue
        if len(content) > 3000:
            content = content[:3000]
        cleaned.append({"role": role, "content": content})
    return cleaned


def _chunk_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def _read_pdf_text(path: Path) -> str:
    if PdfReader is None:
        raise RuntimeError("pypdf is not installed. Run: pip install pypdf")
    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"[Page {i}] {text}")
    return "\n\n".join(pages).strip()


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def _load_materials(materials_dir: Path) -> tuple[list[dict], list[str]]:
    chunks = []
    files = []
    if not materials_dir.exists():
        return chunks, files

    for path in sorted(materials_dir.glob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            continue
        try:
            text = _read_pdf_text(path) if suffix == ".pdf" else _read_text_file(path)
        except Exception as e:
            print(f"[KB] Skip {path.name}: {e}")
            continue
        if not text:
            continue
        files.append(path.name)
        for chunk in _chunk_text(text):
            chunks.append({"source": path.name, "text": chunk, "tokens": _tokenize(chunk)})
    return chunks, files

def _load_feedback(path: Path) -> list[dict]:
    entries = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        question = str(obj.get("question", "")).strip()
        correction = str(obj.get("correction", "")).strip()
        if not question or not correction:
            continue
        entries.append(
            {
                "question": question,
                "badAnswer": str(obj.get("badAnswer", "")).strip(),
                "correction": correction,
                "tokens": _tokenize(f"{question} {correction}"),
                "ts": str(obj.get("ts", "")).strip(),
            }
        )
    return entries

def _append_feedback(path: Path, item: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _reload_kb() -> None:
    global _kb_chunks, _kb_files
    _kb_chunks, _kb_files = _load_materials(_materials_dir)

def _reload_feedback() -> None:
    global _feedback_entries
    _feedback_entries = _load_feedback(_feedback_path)


def _rank_chunks(query: str, top_k: int = 6) -> list[dict]:
    if not _kb_chunks:
        return []
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return _kb_chunks[:top_k]
    scored = []
    for chunk in _kb_chunks:
        score = 0
        for tok in chunk["tokens"]:
            if tok in q_tokens:
                score += 1
        scored.append((score, chunk))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for s, c in scored[:top_k] if s > 0] or [c for _, c in scored[:top_k]]


def _build_context(chunks: list[dict]) -> str:
    return "\n\n".join(
        f"[Source {i + 1}: {chunk['source']}]\n{chunk['text']}" for i, chunk in enumerate(chunks)
    )

def _rank_feedback(query: str, top_k: int = 2) -> list[dict]:
    if not _feedback_entries:
        return []
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return _feedback_entries[-top_k:]
    scored = []
    for item in _feedback_entries:
        score = 0
        for tok in item["tokens"]:
            if tok in q_tokens:
                score += 1
        scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [i for s, i in scored[:top_k] if s > 0]


class AppHandler(SimpleHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path == "/api/chat":
            self._handle_chat()
            return
        if self.path == "/api/feedback":
            self._handle_feedback()
            return
        if self.path == "/api/admin/upload":
            self._handle_admin_upload()
            return
        if self.path == "/api/admin/upload-binary":
            self._handle_admin_upload_binary()
            return
        if self.path == "/api/admin/reload":
            self._handle_admin_reload()
            return
        if self.path == "/api/admin/config":
            self._handle_admin_config_update()
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if self.path == "/api/kb-status":
            self._send_json(
                HTTPStatus.OK,
                {"chunks": len(_kb_chunks), "feedbackCount": len(_feedback_entries)},
            )
            return
        if self.path == "/api/admin/kb-status":
            if not self._check_admin_token():
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
                return
            self._send_json(HTTPStatus.OK, {"files": _kb_files, "chunks": len(_kb_chunks)})
            return
        if self.path == "/api/admin/config":
            if not self._check_admin_token():
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "maxAnswerChars": _max_answer_chars,
                    "defaultPrompt": _default_system_prompt,
                },
            )
            return
        super().do_GET()

    def _read_json_body(self, max_bytes: int) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > max_bytes:
            raise ValueError("Invalid request size")
        raw_body = self.rfile.read(content_length)
        try:
            return json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError("Invalid JSON") from e

    def _check_admin_token(self) -> bool:
        expected = os.environ.get("ADMIN_TOKEN", "").strip()
        provided = self.headers.get("X-Admin-Token", "").strip()
        return bool(expected) and (provided == expected)

    def _handle_chat(self) -> None:
        if not _rate_limit_ok(self.client_address[0]):
            self._send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "Rate limit exceeded"})
            return
        provided_password = self.headers.get("X-User-Password", "").strip()
        if _user_page_password and provided_password != _user_page_password:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
            return

        try:
            payload = self._read_json_body(MAX_CHAT_BODY_BYTES)
        except ValueError as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        system = str(payload.get("system", "")).strip() or _default_system_prompt
        user = str(payload.get("user", "")).strip()
        image_data_url = str(payload.get("imageDataUrl", "")).strip()
        history = _sanitize_history(payload.get("history", []))
        model = str(payload.get("model", OPENAI_MODEL_DEFAULT)).strip() or OPENAI_MODEL_DEFAULT
        kb = payload.get("kb", {})
        kb_enabled = bool(kb.get("enabled")) if isinstance(kb, dict) else False
        kb_query = str(kb.get("query", user)).strip() if isinstance(kb, dict) else user
        kb_top_k = int(kb.get("topK", 6)) if isinstance(kb, dict) else 6
        kb_top_k = max(1, min(kb_top_k, 12))

        if not user:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing 'user'"})
            return
        if _is_unclear_user_query(user) and not history:
            self._send_json(HTTPStatus.OK, {"content": "제가 잘 못 알아들었습니다. 다시 질문해 주세요."})
            return

        if model not in OPENAI_MODEL_ALLOWLIST:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Model not allowed"})
            return

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Server missing OPENAI_API_KEY"})
            return

        user_with_context = user
        feedback_hits = _rank_feedback(user, top_k=2)
        if feedback_hits:
            correction_context = "\n\n".join(
                f"[Correction {idx + 1}]"
                f"\nRelated question: {item['question']}"
                f"\nValidated correction: {item['correction']}"
                for idx, item in enumerate(feedback_hits)
            )
            user_with_context = (
                "Apply these validated correction notes with priority when relevant.\n\n"
                f"{correction_context}\n\n"
                f"Current request:\n{user}"
            )
        if kb_enabled and _kb_chunks:
            top_chunks = _rank_chunks(kb_query, top_k=kb_top_k)
            context = _build_context(top_chunks)
            user_with_context = (
                "Use only the class material context below when answering. "
                "If details are limited, provide the best possible answer and then ask one short follow-up question.\n\n"
                f"Class material context:\n{context}\n\n"
                f"Request:\n{user_with_context}"
            )

        user_content = user_with_context
        if image_data_url:
            if not image_data_url.startswith("data:image/"):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid imageDataUrl format"})
                return
            if len(image_data_url) > 6_000_000:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Image is too large"})
                return
            user_content = [
                {"type": "text", "text": user_with_context},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ]

        messages = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_content})

        upstream_payload = {
            "model": model,
            "temperature": 0.2,
            "messages": messages,
        }

        req = request.Request(
            OPENAI_API_URL,
            data=json.dumps(upstream_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=40) as resp:
                upstream_data = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")
            self._send_json(e.code, {"error": f"OpenAI error: {detail[:500]}"})
            return
        except Exception as e:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": f"Upstream request failed: {e}"})
            return

        text = (
            upstream_data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        if "The context provided is insufficient to answer your request." in text:
            text = (
                "현재 자료 범위에서 확인되는 내용으로 먼저 답변드릴게요. "
                "원하시면 질문 대상을 조금 더 구체화해 주세요."
            )
        text = _remove_material_disclaimer(text)
        if _is_unclear_response(text):
            text = "제가 잘 못 알아들었습니다. 다시 질문해 주세요."
        if _max_answer_chars > 0 and len(text) > _max_answer_chars:
            text = text[:_max_answer_chars].rstrip()
        self._send_json(HTTPStatus.OK, {"content": text or "No response."})

    def _handle_feedback(self) -> None:
        provided_password = self.headers.get("X-User-Password", "").strip()
        if _user_page_password and provided_password != _user_page_password:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
            return
        try:
            payload = self._read_json_body(MAX_FEEDBACK_BODY_BYTES)
        except ValueError as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        question = str(payload.get("question", "")).strip()
        bad_answer = str(payload.get("badAnswer", "")).strip()
        correction = str(payload.get("correction", "")).strip()
        if not question or not correction:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing question or correction"})
            return
        if len(question) > 1000 or len(correction) > 3000:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Feedback too long"})
            return

        item = {
            "question": question,
            "badAnswer": bad_answer[:3000],
            "correction": correction,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            _append_feedback(_feedback_path, item)
            _reload_feedback()
        except Exception as e:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Failed to save feedback: {e}"})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "feedbackCount": len(_feedback_entries)})

    def _handle_admin_upload(self) -> None:
        if not self._check_admin_token():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
            return

        try:
            payload = self._read_json_body(MAX_ADMIN_BODY_BYTES)
        except ValueError as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        filename = str(payload.get("filename", "")).strip()
        content_base64 = str(payload.get("contentBase64", "")).strip()
        if not filename or not content_base64:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing filename or contentBase64"})
            return

        safe_name = Path(filename).name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "File type not allowed"})
            return

        try:
            data = base64.b64decode(content_base64, validate=True)
        except Exception:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid base64 file content"})
            return

        _materials_dir.mkdir(parents=True, exist_ok=True)
        out_path = _materials_dir / safe_name
        try:
            out_path.write_bytes(data)
            _reload_kb()
        except Exception as e:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Failed to save file: {e}"})
            return

        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "saved": safe_name,
                "files": _kb_files,
                "chunks": len(_kb_chunks),
            },
        )

    def _handle_admin_upload_binary(self) -> None:
        if not self._check_admin_token():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
            return

        filename = unquote(self.headers.get("X-Filename", "")).strip()
        safe_name = Path(filename).name
        suffix = Path(safe_name).suffix.lower()
        if not safe_name or suffix not in ALLOWED_EXTENSIONS:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "File type not allowed"})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > MAX_ADMIN_UPLOAD_BYTES:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid upload size"})
            return

        data = self.rfile.read(content_length)
        if not data:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Empty upload"})
            return

        _materials_dir.mkdir(parents=True, exist_ok=True)
        out_path = _materials_dir / safe_name
        try:
            out_path.write_bytes(data)
            _reload_kb()
        except Exception as e:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Failed to save file: {e}"})
            return

        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "saved": safe_name,
                "files": _kb_files,
                "chunks": len(_kb_chunks),
            },
        )

    def _handle_admin_reload(self) -> None:
        if not self._check_admin_token():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
            return
        _reload_kb()
        self._send_json(HTTPStatus.OK, {"ok": True, "files": _kb_files, "chunks": len(_kb_chunks)})

    def _handle_admin_config_update(self) -> None:
        if not self._check_admin_token():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
            return
        try:
            payload = self._read_json_body(20_000)
        except ValueError as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        global _max_answer_chars, _default_system_prompt

        if "maxAnswerChars" in payload:
            try:
                value = int(payload.get("maxAnswerChars", 0))
            except Exception:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "maxAnswerChars must be an integer"})
                return

            if value < 0 or value > 20_000:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "maxAnswerChars must be between 0 and 20000"},
                )
                return
            _max_answer_chars = value

        if "defaultPrompt" in payload:
            prompt = str(payload.get("defaultPrompt", "")).strip()
            if not prompt:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "defaultPrompt cannot be empty"})
                return
            if len(prompt) > 10_000:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "defaultPrompt is too long"})
                return
            _default_system_prompt = prompt

        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "maxAnswerChars": _max_answer_chars,
                "defaultPrompt": _default_system_prompt,
            },
        )

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument("--materials-dir", type=str, default=str(DEFAULT_MATERIALS_DIR))
    parser.add_argument("--feedback-path", type=str, default=str(DEFAULT_FEEDBACK_PATH))
    args = parser.parse_args()

    global _materials_dir, _feedback_path
    _materials_dir = Path(args.materials_dir)
    _feedback_path = Path(args.feedback_path)

    _reload_kb()
    _reload_feedback()

    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"Serving on http://localhost:{args.port}")
    print("User page: /")
    print("Admin page: /admin.html")
    print(f"MAX_ANSWER_CHARS: {_max_answer_chars}")
    print(f"DEFAULT_SYSTEM_PROMPT length: {len(_default_system_prompt)}")
    print(f"USER_PAGE_PASSWORD enabled: {bool(_user_page_password)}")
    print(f"Knowledge base files: {len(_kb_files)}, chunks: {len(_kb_chunks)}")
    print(f"Feedback entries: {len(_feedback_entries)}")
    if _kb_files:
        print("Loaded:", ", ".join(_kb_files))
    else:
        print(f"No materials found in '{_materials_dir}'")
    server.serve_forever()


if __name__ == "__main__":
    main()
