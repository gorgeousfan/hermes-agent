#!/usr/bin/env python3
"""Playwright Tool Module

Provides Playwright-based browser automation with per-task sessions.
Uses Python Playwright sync API for in-process stateful control.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_constants import get_hermes_home
from tools.registry import registry

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright, Error as PlaywrightError
except Exception:  # pragma: no cover
    sync_playwright = None
    PlaywrightError = Exception


_lock = threading.Lock()
_playwright = None
_sessions: Dict[str, Dict[str, Any]] = {}


# ------------------------
# helpers
# ------------------------

def _json_error(msg: str) -> str:
    return json.dumps({"error": msg})


def _get_task_id(kw: dict) -> str:
    return kw.get("task_id") or "default"


def _ensure_playwright():
    global _playwright
    if sync_playwright is None:
        raise RuntimeError("playwright is not installed")
    if _playwright is None:
        _playwright = sync_playwright().start()
    return _playwright


def _get_session(task_id: str) -> Optional[Dict[str, Any]]:
    return _sessions.get(task_id)


def _ensure_session(task_id: str, **opts) -> Dict[str, Any]:
    session = _get_session(task_id)
    if session:
        return session

    p = _ensure_playwright()
    browser_name = opts.get("browser", "chromium")
    headless = bool(opts.get("headless", True))
    proxy = opts.get("proxy")

    if browser_name not in ("chromium", "firefox", "webkit"):
        raise RuntimeError("browser must be chromium|firefox|webkit")

    browser_type = getattr(p, browser_name)
    launch_args: Dict[str, Any] = {"headless": headless}
    if proxy:
        launch_args["proxy"] = proxy

    browser = browser_type.launch(**launch_args)

    context_args: Dict[str, Any] = {}
    viewport = opts.get("viewport")
    if viewport:
        context_args["viewport"] = viewport
    user_agent = opts.get("user_agent")
    if user_agent:
        context_args["user_agent"] = user_agent
    locale = opts.get("locale")
    if locale:
        context_args["locale"] = locale
    timezone_id = opts.get("timezone_id")
    if timezone_id:
        context_args["timezone_id"] = timezone_id
    storage_state_path = opts.get("storage_state_path")
    if storage_state_path:
        context_args["storage_state"] = storage_state_path
    ignore_https_errors = opts.get("ignore_https_errors")
    if ignore_https_errors is not None:
        context_args["ignore_https_errors"] = bool(ignore_https_errors)
    permissions = opts.get("permissions")
    if permissions:
        context_args["permissions"] = permissions

    context = browser.new_context(**context_args)
    page = context.new_page()

    session = {
        "browser_name": browser_name,
        "browser": browser,
        "context": context,
        "page": page,
        "created_at": time.time(),
        "route_blocked": False,
    }
    _sessions[task_id] = session
    return session


def _get_page(task_id: str, **opts):
    session = _ensure_session(task_id, **opts)
    return session["page"]


def _safe_path(ext: str) -> Path:
    base = get_hermes_home() / "cache" / "playwright"
    base.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    return base / f"pw_{ts}.{ext}"


def _parse_json(val):
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return None


def check_playwright_requirements() -> bool:
    return sync_playwright is not None


# ------------------------
# handlers
# ------------------------

def pw_start(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        _ensure_session(task_id, **args)
        return json.dumps({"ok": True, "task_id": task_id})
    except Exception as e:
        return _json_error(str(e))


def pw_navigate(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        url = args.get("url") or ""
        if not url:
            return _json_error("url is required")
        wait_until = args.get("wait_until") or "load"
        timeout_ms = args.get("timeout_ms")
        page = _get_page(task_id, **args)
        page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        return json.dumps({"ok": True, "url": page.url})
    except Exception as e:
        return _json_error(str(e))


def pw_click(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        selector = args.get("selector") or ""
        if not selector:
            return _json_error("selector is required")
        page = _get_page(task_id)
        page.click(selector, button=args.get("button"), click_count=args.get("click_count"), timeout=args.get("timeout_ms"))
        return json.dumps({"ok": True})
    except Exception as e:
        return _json_error(str(e))


def pw_type(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        selector = args.get("selector") or ""
        text = args.get("text") or ""
        if not selector:
            return _json_error("selector is required")
        page = _get_page(task_id)
        page.type(selector, text, delay=args.get("delay"))
        return json.dumps({"ok": True})
    except Exception as e:
        return _json_error(str(e))


def pw_fill(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        selector = args.get("selector") or ""
        text = args.get("text") or ""
        if not selector:
            return _json_error("selector is required")
        page = _get_page(task_id)
        page.fill(selector, text)
        return json.dumps({"ok": True})
    except Exception as e:
        return _json_error(str(e))


def pw_press(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        key = args.get("key") or ""
        selector = args.get("selector")
        if not key:
            return _json_error("key is required")
        page = _get_page(task_id)
        if selector:
            page.press(selector, key)
        else:
            page.keyboard.press(key)
        return json.dumps({"ok": True})
    except Exception as e:
        return _json_error(str(e))


def pw_wait_for_selector(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        selector = args.get("selector") or ""
        if not selector:
            return _json_error("selector is required")
        page = _get_page(task_id)
        handle = page.wait_for_selector(selector, state=args.get("state"), timeout=args.get("timeout_ms"))
        return json.dumps({"ok": True, "found": handle is not None})
    except Exception as e:
        return _json_error(str(e))


def pw_eval(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        script = args.get("script") or ""
        if not script:
            return _json_error("script is required")
        arg = _parse_json(args.get("arg_json"))
        page = _get_page(task_id)
        result = page.evaluate(script, arg) if arg is not None else page.evaluate(script)
        return json.dumps({"ok": True, "result": result})
    except Exception as e:
        return _json_error(str(e))


def pw_screenshot(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        path = args.get("path")
        if not path:
            path = str(_safe_path("png"))
        page = _get_page(task_id)
        page.screenshot(path=path, full_page=bool(args.get("full_page", True)))
        return json.dumps({"ok": True, "path": path})
    except Exception as e:
        return _json_error(str(e))


def pw_pdf(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        path = args.get("path") or str(_safe_path("pdf"))
        page = _get_page(task_id)
        if _sessions[task_id]["browser_name"] != "chromium":
            return _json_error("pdf is only supported in chromium")
        page.pdf(path=path, format=args.get("format"))
        return json.dumps({"ok": True, "path": path})
    except Exception as e:
        return _json_error(str(e))


def pw_content(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        page = _get_page(task_id)
        html = page.content()
        return json.dumps({"ok": True, "content": html})
    except Exception as e:
        return _json_error(str(e))


def pw_text(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        selector = args.get("selector") or "body"
        page = _get_page(task_id)
        text = page.inner_text(selector)
        max_len = int(args.get("max_len") or 12000)
        if len(text) > max_len:
            text = text[:max_len] + "..."
        return json.dumps({"ok": True, "text": text})
    except Exception as e:
        return _json_error(str(e))


def pw_set_cookies(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        cookies = args.get("cookies")
        if not cookies:
            return _json_error("cookies is required")
        session = _ensure_session(task_id)
        session["context"].add_cookies(cookies)
        return json.dumps({"ok": True})
    except Exception as e:
        return _json_error(str(e))


def pw_get_cookies(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        session = _ensure_session(task_id)
        cookies = session["context"].cookies()
        return json.dumps({"ok": True, "cookies": cookies})
    except Exception as e:
        return _json_error(str(e))


def pw_set_viewport(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        viewport = args.get("viewport")
        if not viewport:
            return _json_error("viewport is required")
        session = _ensure_session(task_id)
        session["page"].set_viewport_size(viewport)
        return json.dumps({"ok": True})
    except Exception as e:
        return _json_error(str(e))


def pw_route_block(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        patterns = args.get("patterns") or []
        if not patterns:
            return _json_error("patterns is required")
        session = _ensure_session(task_id)
        page = session["page"]
        if not session.get("route_blocked"):
            for pat in patterns:
                page.route(pat, lambda route: route.abort())
            session["route_blocked"] = True
        return json.dumps({"ok": True, "patterns": patterns})
    except Exception as e:
        return _json_error(str(e))


def pw_info(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        session = _ensure_session(task_id)
        page = session["page"]
        return json.dumps({
            "ok": True,
            "url": page.url,
            "title": page.title(),
            "browser": session["browser_name"],
        })
    except Exception as e:
        return _json_error(str(e))


def pw_close(args: dict, **kw) -> str:
    try:
        task_id = _get_task_id(kw)
        close_all = bool(args.get("all", False))
        if close_all:
            for sid in list(_sessions.keys()):
                _close_session(sid)
            return json.dumps({"ok": True, "closed": "all"})
        _close_session(task_id)
        return json.dumps({"ok": True, "closed": task_id})
    except Exception as e:
        return _json_error(str(e))


def _close_session(task_id: str):
    session = _sessions.pop(task_id, None)
    if not session:
        return
    try:
        session["page"].close()
    except Exception:
        pass
    try:
        session["context"].close()
    except Exception:
        pass
    try:
        session["browser"].close()
    except Exception:
        pass


# ------------------------
# schemas + registry
# ------------------------

PW_START_SCHEMA = {
    "name": "pw_start",
    "description": "Start a Playwright session (per task).",
    "parameters": {
        "type": "object",
        "properties": {
            "browser": {"type": "string", "enum": ["chromium", "firefox", "webkit"], "default": "chromium"},
            "headless": {"type": "boolean", "default": True},
            "viewport": {"type": "object", "properties": {"width": {"type": "integer"}, "height": {"type": "integer"}}},
            "user_agent": {"type": "string"},
            "locale": {"type": "string"},
            "timezone_id": {"type": "string"},
            "storage_state_path": {"type": "string"},
            "proxy": {"type": "object", "properties": {"server": {"type": "string"}, "username": {"type": "string"}, "password": {"type": "string"}}},
            "ignore_https_errors": {"type": "boolean"},
            "permissions": {"type": "array", "items": {"type": "string"}}
        }
    }
}

PW_NAV_SCHEMA = {
    "name": "pw_navigate",
    "description": "Navigate to a URL.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "wait_until": {"type": "string", "enum": ["load", "domcontentloaded", "networkidle"], "default": "load"},
            "timeout_ms": {"type": "integer"},
            "browser": {"type": "string"},
            "headless": {"type": "boolean"}
        },
        "required": ["url"]
    }
}

PW_CLICK_SCHEMA = {
    "name": "pw_click",
    "description": "Click an element by CSS selector.",
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "click_count": {"type": "integer"},
            "timeout_ms": {"type": "integer"}
        },
        "required": ["selector"]
    }
}

PW_TYPE_SCHEMA = {
    "name": "pw_type",
    "description": "Type text into an element.",
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "delay": {"type": "integer"}
        },
        "required": ["selector", "text"]
    }
}

PW_FILL_SCHEMA = {
    "name": "pw_fill",
    "description": "Fill an input element (clears then types).",
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "text": {"type": "string"}
        },
        "required": ["selector", "text"]
    }
}

PW_PRESS_SCHEMA = {
    "name": "pw_press",
    "description": "Press a keyboard key (optionally on selector).",
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "key": {"type": "string"}
        },
        "required": ["key"]
    }
}

PW_WAIT_SCHEMA = {
    "name": "pw_wait_for_selector",
    "description": "Wait for selector to appear.",
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "state": {"type": "string", "enum": ["attached", "detached", "visible", "hidden"]},
            "timeout_ms": {"type": "integer"}
        },
        "required": ["selector"]
    }
}

PW_EVAL_SCHEMA = {
    "name": "pw_eval",
    "description": "Evaluate JS in page context.",
    "parameters": {
        "type": "object",
        "properties": {
            "script": {"type": "string"},
            "arg_json": {"type": "string"}
        },
        "required": ["script"]
    }
}

PW_SCREENSHOT_SCHEMA = {
    "name": "pw_screenshot",
    "description": "Take a screenshot.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "full_page": {"type": "boolean", "default": True}
        }
    }
}

PW_PDF_SCHEMA = {
    "name": "pw_pdf",
    "description": "Save page as PDF (chromium only).",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "format": {"type": "string"}
        }
    }
}

PW_CONTENT_SCHEMA = {
    "name": "pw_content",
    "description": "Get HTML content.",
    "parameters": {"type": "object", "properties": {}}
}

PW_TEXT_SCHEMA = {
    "name": "pw_text",
    "description": "Get visible text (default: body).",
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "default": "body"},
            "max_len": {"type": "integer", "default": 12000}
        }
    }
}

PW_SET_COOKIES_SCHEMA = {
    "name": "pw_set_cookies",
    "description": "Set cookies in context.",
    "parameters": {
        "type": "object",
        "properties": {"cookies": {"type": "array", "items": {"type": "object"}}},
        "required": ["cookies"]
    }
}

PW_GET_COOKIES_SCHEMA = {
    "name": "pw_get_cookies",
    "description": "Get cookies from context.",
    "parameters": {"type": "object", "properties": {}}
}

PW_SET_VIEWPORT_SCHEMA = {
    "name": "pw_set_viewport",
    "description": "Set viewport size.",
    "parameters": {
        "type": "object",
        "properties": {"viewport": {"type": "object", "properties": {"width": {"type": "integer"}, "height": {"type": "integer"}}}},
        "required": ["viewport"]
    }
}

PW_ROUTE_BLOCK_SCHEMA = {
    "name": "pw_route_block",
    "description": "Block network requests by URL pattern (string or glob).",
    "parameters": {
        "type": "object",
        "properties": {"patterns": {"type": "array", "items": {"type": "string"}}},
        "required": ["patterns"]
    }
}

PW_INFO_SCHEMA = {
    "name": "pw_info",
    "description": "Get current page info.",
    "parameters": {"type": "object", "properties": {}}
}

PW_CLOSE_SCHEMA = {
    "name": "pw_close",
    "description": "Close current session (or all).",
    "parameters": {
        "type": "object",
        "properties": {"all": {"type": "boolean", "default": False}}
    }
}


registry.register(
    name="pw_start",
    toolset="playwright",
    schema=PW_START_SCHEMA,
    handler=pw_start,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_navigate",
    toolset="playwright",
    schema=PW_NAV_SCHEMA,
    handler=pw_navigate,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_click",
    toolset="playwright",
    schema=PW_CLICK_SCHEMA,
    handler=pw_click,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_type",
    toolset="playwright",
    schema=PW_TYPE_SCHEMA,
    handler=pw_type,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_fill",
    toolset="playwright",
    schema=PW_FILL_SCHEMA,
    handler=pw_fill,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_press",
    toolset="playwright",
    schema=PW_PRESS_SCHEMA,
    handler=pw_press,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_wait_for_selector",
    toolset="playwright",
    schema=PW_WAIT_SCHEMA,
    handler=pw_wait_for_selector,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_eval",
    toolset="playwright",
    schema=PW_EVAL_SCHEMA,
    handler=pw_eval,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_screenshot",
    toolset="playwright",
    schema=PW_SCREENSHOT_SCHEMA,
    handler=pw_screenshot,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_pdf",
    toolset="playwright",
    schema=PW_PDF_SCHEMA,
    handler=pw_pdf,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_content",
    toolset="playwright",
    schema=PW_CONTENT_SCHEMA,
    handler=pw_content,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_text",
    toolset="playwright",
    schema=PW_TEXT_SCHEMA,
    handler=pw_text,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_set_cookies",
    toolset="playwright",
    schema=PW_SET_COOKIES_SCHEMA,
    handler=pw_set_cookies,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_get_cookies",
    toolset="playwright",
    schema=PW_GET_COOKIES_SCHEMA,
    handler=pw_get_cookies,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_set_viewport",
    toolset="playwright",
    schema=PW_SET_VIEWPORT_SCHEMA,
    handler=pw_set_viewport,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_route_block",
    toolset="playwright",
    schema=PW_ROUTE_BLOCK_SCHEMA,
    handler=pw_route_block,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_info",
    toolset="playwright",
    schema=PW_INFO_SCHEMA,
    handler=pw_info,
    check_fn=check_playwright_requirements,
)

registry.register(
    name="pw_close",
    toolset="playwright",
    schema=PW_CLOSE_SCHEMA,
    handler=pw_close,
    check_fn=check_playwright_requirements,
)
