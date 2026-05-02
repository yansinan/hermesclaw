"""HermesClaw v2: multi-gateway proxy router for WeChat.

Takes over one iLink token, polls for messages, and distributes them to
configurable proxy servers. Each gateway believes it is talking directly to
the iLink API.
"""

import json
import logging
import os
import secrets
import signal
import sys
import threading
import time
from enum import Enum
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

# Use Python builtin urllib only (remove dependency on `requests`).
import urllib.error as _ue
import urllib.request as _ur


class _Resp:
    def __init__(self, code, data, headers):
        self.status_code = code
        self.text = data
        self.content = data.encode("utf-8", errors="ignore")
        self.headers = headers

    def json(self):
        try:
            return json.loads(self.text) if self.text else {}
        except Exception:
            return {}

    def raise_for_status(self):
        if getattr(self, "status_code", 0) >= 400:
            raise Exception(f"HTTP {getattr(self, 'status_code', '??')}")


class _Exceptions:
    class Timeout(Exception):
        pass

    class RequestException(Exception):
        pass


def _post(url, headers=None, data=None, timeout=None):
    if isinstance(data, str):
        body = data.encode("utf-8")
    else:
        body = data
    req = _ur.Request(url, data=body, headers=headers or {}, method="POST")
    try:
        with _ur.urlopen(req, timeout=timeout) as r:
            b = r.read()
            text = b.decode("utf-8", errors="ignore")
            return _Resp(r.getcode(), text, dict(r.getheaders()))
    except _ue.URLError as e:
        # timeout or other network errors
        if isinstance(getattr(e, "reason", None), TimeoutError):
            raise _Exceptions.Timeout(e)
        raise _Exceptions.RequestException(e)


# Expose a requests-like module API (post + exceptions) but backed by urllib
class _ReqModule:
    post = staticmethod(_post)
    exceptions = _Exceptions


requests = _ReqModule()

import re
from collections import deque

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

T, VO = 1, 3  # iLink message types: text, voice
ILINK_VER = "2.1.7"
ILINK_CV = "65547"
QUEUE_CAP = 200
DEFAULT_POLL_SEC = 35

log = logging.getLogger("hermesclaw")

DEFAULT_LEGACY_AGENTS = [
    {
        "name": "hermes",
        "host": "0.0.0.0",
        "port": 19998,
        "tag": "[Hermes Agent]",
        "enabled": True,
    },
    {
        "name": "openclaw",
        "host": "0.0.0.0",
        "port": 19999,
        "tag": "[OpenClaw]",
        "enabled": True,
    },
]

# ---------------------------------------------------------------------------
# Route enum & persistent state
# ---------------------------------------------------------------------------


class Route(str, Enum):
    HERMES = "hermes"
    OPENCLAW = "openclaw"
    BOTH = "both"


def _env_bool(env, key, default=False):
    raw = str(env.get(key, "true" if default else "false")).lower()
    return raw in ("true", "1", "yes", "on")


def _coerce_name(name):
    if not isinstance(name, str):
        return ""
    return name.strip().lower()


def _coerce_port(value, fallback):
    try:
        return int(value)
    except Exception:
        return int(fallback)


def _legacy_agents_from_env(env):
    return {
        "version": 1,
        "default_route": "hermes",
        "groups": {},
        "agents": [
            {
                "name": "hermes",
                "host": env.get("HERMES_PROXY_HOST", "0.0.0.0"),
                "port": _coerce_port(env.get("HERMES_PROXY_PORT", "19998"), 19998),
                "tag": "[Hermes Agent]",
                "enabled": _env_bool(env, "HERMES_ENABLED", default=True),
            },
            {
                "name": "openclaw",
                "host": env.get("OPENCLAW_PROXY_HOST", "0.0.0.0"),
                "port": _coerce_port(env.get("OPENCLAW_PROXY_PORT", "19999"), 19999),
                "tag": "[OpenClaw]",
                "enabled": _env_bool(env, "OPENCLAW_ENABLED", default=True),
            },
        ],
    }


def _load_json_file(fp):
    return json.loads(Path(fp).read_text(encoding="utf-8"))


def _resolve_path(raw_path, base_dir):
    """Resolve env-configured path with support for relative paths."""
    p = Path(os.path.expandvars(str(raw_path))).expanduser()
    if not p.is_absolute():
        p = Path(base_dir) / p
    return p


def _normalize_agents_config(raw):
    agents = raw.get("agents", []) if isinstance(raw, dict) else []
    if not isinstance(agents, list):
        raise ValueError("agents must be a list")

    cleaned = []
    for idx, agent in enumerate(agents):
        if not isinstance(agent, dict):
            log.warning("Skipping invalid agent entry at index=%d", idx)
            continue
        name = _coerce_name(agent.get("name", ""))
        if not name:
            log.warning("Skipping unnamed agent at index=%d", idx)
            continue
        entry = {
                "name": name,
                "host": str(agent.get("host", "0.0.0.0") or "0.0.0.0"),
                "port": _coerce_port(agent.get("port", 0), 0),
                "tag": str(agent.get("tag", f"[{name}]") or f"[{name}]"),
                "enabled": bool(agent.get("enabled", True)),
            }
        if agent.get("aliases"):
            entry["aliases"] = list(agent["aliases"])
        cleaned.append(entry)

    if not cleaned:
        cleaned = [dict(x) for x in DEFAULT_LEGACY_AGENTS]

    seen_names = set()
    for agent in cleaned:
        if agent["name"] in seen_names:
            raise ValueError(f"duplicate agent name: {agent['name']}")
        seen_names.add(agent["name"])

    enabled = [a for a in cleaned if a.get("enabled")]
    if not enabled:
        raise ValueError("no enabled agents in config")

    seen_ports = set()
    for agent in enabled:
        p = int(agent.get("port", 0))
        if p <= 0:
            raise ValueError(f"invalid port for agent {agent['name']}: {p}")
        if p in seen_ports:
            raise ValueError(f"duplicate enabled port: {p}")
        seen_ports.add(p)

    default_route = _coerce_name(raw.get("default_route", "")) if isinstance(raw, dict) else ""
    enabled_names = [a["name"] for a in enabled]
    if default_route not in enabled_names:
        default_route = enabled_names[0]

    groups_in = raw.get("groups", {}) if isinstance(raw, dict) else {}
    groups = {}        # {name: [member_names]}
    group_aliases = {}  # {name: [alias_strings]}
    if isinstance(groups_in, dict):
        for k, v in groups_in.items():
            gname = _coerce_name(k)
            if not gname:
                continue
            # New format: {"members": [...], "aliases": [...]}
            if isinstance(v, dict):
                members_raw = v.get("members", [])
                aliases_raw_g = v.get("aliases", [])
            elif isinstance(v, list):
                # Old format: plain list of member names
                members_raw = v
                aliases_raw_g = []
            else:
                continue
            members = []
            for m in members_raw:
                n = _coerce_name(m)
                if n and n in enabled_names and n not in members:
                    members.append(n)
            if members:
                groups[gname] = members
            if aliases_raw_g:
                group_aliases[gname] = aliases_raw_g

    if "all" not in groups:
        groups["all"] = enabled_names

    return {
        "version": 1,
        "default_route": default_route,
        "groups": groups,
        "group_aliases": group_aliases,
        "agents": cleaned,
    }


def load_agents_config(env=None, base_dir=None):
    env = env or os.environ
    base = Path(base_dir) if base_dir else Path(__file__).parent

    raw = None
    source = "legacy_env"
    default_fp = base / "agents.json"
    if default_fp.exists():
        raw = _load_json_file(default_fp)
        source = f"default_file:{default_fp}"
    else:
        cfg_file = env.get("AGENTS_CONFIG_FILE", "").strip()
        if cfg_file:
            fp = _resolve_path(cfg_file, base)
            raw = _load_json_file(fp)
            source = f"env_file:{fp}"
        else:
            inline = env.get("AGENTS_CONFIG", "").strip()
            if inline:
                raw = json.loads(inline)
                source = "env_inline:AGENTS_CONFIG"
            else:
                raw = _legacy_agents_from_env(env)
                source = "legacy_env:HERMES_/OPENCLAW_"

    cfg = _normalize_agents_config(raw)
    cfg["config_source"] = source

    # Optional runtime settings carried by JSON/inline config.
    if isinstance(raw, dict):
        if raw.get("ilink_base_url"):
            cfg["ilink_base_url"] = str(raw.get("ilink_base_url")).strip()
        if raw.get("ilink_token"):
            cfg["ilink_token"] = str(raw.get("ilink_token")).strip()
        if raw.get("admin_ilink_uid"):
            cfg["admin_ilink_uid"] = str(raw.get("admin_ilink_uid")).strip()

    # Build aliases_raw from multiple sources (priority: top-level mention_aliases
    # for backward compat, then per-agent aliases + per-group aliases from new format).
    aliases_built = {}

    if isinstance(raw, dict) and raw.get("mention_aliases"):
        # Backward-compat: top-level mention_aliases blob
        merged = dict(raw["mention_aliases"])
        if "_groups" not in merged:
            merged["_groups"] = cfg.get("groups", {})
        cfg["aliases_raw"] = merged
        return cfg

    # Per-agent aliases
    for agent in cfg.get("agents", []):
        if not agent.get("enabled", True):
            continue
        aname = agent.get("name", "")
        if aname and agent.get("aliases"):
            aliases_built[aname] = list(agent["aliases"])

    # Per-group aliases (e.g. all -> ["both", "@all"])
    for gname, galiases in cfg.get("group_aliases", {}).items():
        if galiases:
            aliases_built[gname] = list(galiases)

    if aliases_built:
        aliases_built["_groups"] = cfg.get("groups", {})
        cfg["aliases_raw"] = aliases_built

    return cfg


class AgentRegistry:
    def __init__(self, config):
        self.config = config
        self.agents = config.get("agents", [])
        self.enabled_agents = {
            a["name"]: a for a in self.agents if a.get("enabled", True)
        }
        self.enabled_names = list(self.enabled_agents.keys())
        self.groups = config.get("groups", {}) or {}
        self.default_route = config.get("default_route") or (
            self.enabled_names[0] if self.enabled_names else "hermes"
        )
        self.aliases_raw = config.get("aliases_raw")

    def display_name(self, name):
        n = _coerce_name(name)
        if n == "hermes":
            return "Hermes"
        if n == "openclaw":
            return "OpenClaw"
        return n

    def is_valid_agent(self, name):
        return _coerce_name(name) in self.enabled_agents

    def route_targets(self, route_spec):
        if route_spec is None:
            return [self.default_route]

        if isinstance(route_spec, Route):
            route_spec = route_spec.value

        if isinstance(route_spec, str):
            spec = _coerce_name(route_spec)
            if spec in ("both", "all"):
                return list(self.groups.get("all", self.enabled_names))
            if spec.startswith("group:"):
                g = _coerce_name(spec.split(":", 1)[1])
                return list(self.groups.get(g, []))
            if spec in self.enabled_agents:
                return [spec]
            return []

        if isinstance(route_spec, dict):
            if "group" in route_spec:
                g = _coerce_name(route_spec.get("group"))
                return list(self.groups.get(g, []))
            if "agents" in route_spec and isinstance(route_spec["agents"], list):
                out = []
                for a in route_spec["agents"]:
                    n = _coerce_name(a)
                    if n in self.enabled_agents and n not in out:
                        out.append(n)
                return out
            return []

        if isinstance(route_spec, (list, tuple, set)):
            out = []
            for a in route_spec:
                n = _coerce_name(a)
                if n in self.enabled_agents and n not in out:
                    out.append(n)
            return out

        return []


def _registry_from_legacy_queues(hermes_q=None, openclaw_q=None):
    agents = []
    if hermes_q is not None:
        agents.append(
            {
                "name": "hermes",
                "host": "0.0.0.0",
                "port": 19998,
                "tag": "[Hermes Agent]",
                "enabled": True,
            }
        )
    if openclaw_q is not None:
        agents.append(
            {
                "name": "openclaw",
                "host": "0.0.0.0",
                "port": 19999,
                "tag": "[OpenClaw]",
                "enabled": True,
            }
        )
    if not agents:
        agents = [dict(x) for x in DEFAULT_LEGACY_AGENTS]
    cfg = _normalize_agents_config(
        {
            "agents": agents,
            "default_route": agents[0]["name"],
            "groups": {"all": [x["name"] for x in agents]},
        }
    )
    return AgentRegistry(cfg)


class State:
    """Per-user routing state, persisted to JSON."""

    def __init__(self, fp, default_route="hermes"):
        self.fp = Path(fp)
        self.d = {}
        self.lock = threading.Lock()
        self.default_route = _coerce_name(default_route) or "hermes"
        if self.fp.exists():
            try:
                self.d = json.loads(self.fp.read_text())
                log.info("Loaded %d route states", len(self.d))
            except Exception:
                pass

    def save(self):
        t = self.fp.with_suffix(".tmp")
        t.write_text(json.dumps(self.d, indent=2, ensure_ascii=False))
        t.replace(self.fp)

    def get(self, uid, default_route=None):
        with self.lock:
            default = _coerce_name(default_route or self.default_route) or "hermes"
            if uid not in self.d:
                self.d[uid] = {"route": default, "status_shown": False}
            entry = self.d[uid]
            # Migrate v1 format {"b": ...} to v2 {"route": ...}
            if "route" not in entry and "b" in entry:
                entry["route"] = entry.get("b", default)
            raw = entry.get("route", default)
            if isinstance(raw, Route):
                return raw
            if isinstance(raw, str):
                norm = _coerce_name(raw) or default
                if norm in (Route.HERMES.value, Route.OPENCLAW.value, Route.BOTH.value):
                    return Route(norm)
                return norm
            return Route(default) if default in (Route.HERMES.value, Route.OPENCLAW.value, Route.BOTH.value) else default

    def should_show_status(self, uid):
        with self.lock:
            if uid not in self.d:
                self.d[uid] = {"route": self.default_route, "status_shown": False}
            return not self.d[uid].get("status_shown", False)

    def mark_status_shown(self, uid):
        with self.lock:
            if uid not in self.d:
                self.d[uid] = {"route": self.default_route, "status_shown": True}
            else:
                self.d[uid]["status_shown"] = True
            self.save()

    def set(self, uid, route):
        if isinstance(route, Route):
            route = route.value
        route_value = _coerce_name(route)
        if not route_value:
            route_value = self.default_route
        with self.lock:
            status_shown = self.d.get(uid, {}).get("status_shown", False)
            self.d[uid] = {"route": route_value, "status_shown": status_shown}
            self.save()
            log.info("User %s -> %s", uid[:16], route_value)


# ---------------------------------------------------------------------------
# Text extraction (voice -> transcription)
# ---------------------------------------------------------------------------


def extract_text(items):
    """Return combined text from iLink items.

    Voice items contribute only their iLink transcription by design.
    """
    parts = []
    for it in items:
        tp = it.get("type", 0)
        if tp == T:
            x = it.get("text_item", {}).get("text", "")
            if x:
                parts.append(x)
        elif tp == VO:
            x = it.get("voice_item", {}).get("text", "")
            if x:
                parts.append(
                    f'[The user sent a voice message. Here\'s what they said: "{x}"]'
                )
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Router commands
# ---------------------------------------------------------------------------


def route_label(route_spec, registry=None):
    reg = registry or _registry_from_legacy_queues(hermes_q=True, openclaw_q=True)
    if isinstance(route_spec, Route):
        route_spec = route_spec.value
    if isinstance(route_spec, str):
        s = _coerce_name(route_spec)
        if s in ("both", "all"):
            return " + ".join([reg.display_name(x) for x in reg.route_targets("both")])
        if s in reg.enabled_agents:
            return reg.display_name(s)
    if isinstance(route_spec, dict) and "group" in route_spec:
        g = _coerce_name(route_spec.get("group"))
        members = [reg.display_name(x) for x in reg.groups.get(g, [])]
        return f"group:{g} ({', '.join(members)})"
    if isinstance(route_spec, (list, tuple, set)):
        names = []
        for x in route_spec:
            n = _coerce_name(x)
            if n:
                names.append(reg.display_name(n))
        return " + ".join(names)
    return str(route_spec)


def _status_text(state, uid, registry):
    current = state.get(uid, default_route=registry.default_route)
    lines = [
        "**HermesClaw v2** by X @AaronYonW",
        f"**Current route**: **{route_label(current, registry)}**",
    ]
    for name in registry.enabled_names:
        lines.append(f"**/{name}** -> {registry.display_name(name)} only")
    lines.append("**/both** -> default group (all)")
    lines.append("**/whoami**, **/w** -> this status")
    return "\n".join(lines)


def cmd(state, uid, text, registry=None):
    """Process a slash command.  Returns reply text or None for passthrough."""
    reg = registry or _registry_from_legacy_queues(hermes_q=True, openclaw_q=True)
    c = text.strip().lower()
    if c in ("/w", "/whoami"):
        return _status_text(state, uid, reg)
    if not c.startswith("/"):
        return None
    route_cmd = c[1:].split()[0]
    if route_cmd in ("both", "all"):
        state.set(uid, "both")
        return f"Switched to **{route_label('both', reg)}**."
    if reg.is_valid_agent(route_cmd):
        state.set(uid, route_cmd)
        return f"Switched to **{reg.display_name(route_cmd)}**."
    return None


# ---------------------------------------------------------------------------
# iLink helpers
# ---------------------------------------------------------------------------


def hdrs(tok, body=""):
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode())),
        "iLink-App-Id": "",
        "iLink-App-ClientVersion": ILINK_CV,
        "Authorization": "Bearer " + tok if tok else "",
    }


def ilink_post(base_url, ep, bd, tok, to=30):
    """POST JSON to iLink and return parsed JSON.

    Be defensive: some runtime HTTP clients may return response-like objects
    that don't implement requests.Response.raise_for_status. Don't call
    raise_for_status(); instead inspect status_code (or getcode) and treat
    >=400 as an error.
    """
    url = base_url.rstrip("/") + "/" + ep.lstrip("/")
    bs = json.dumps(bd)
    r = requests.post(url, headers=hdrs(tok, bs), data=bs.encode(), timeout=to)

    # Determine numeric status code in a safe, attribute-checked way.
    status = None
    try:
        status = getattr(r, 'status_code', None)
    except Exception:
        status = None
    if status is None:
        # sometimes response objects expose .getcode() (urllib)
        try:
            status = getattr(r, 'getcode', lambda: None)()
        except Exception:
            status = None
    if status is not None and int(status) >= 400:
        raise Exception(f"HTTP {status}")

    # Finally parse JSON body if available. Fall back to empty dict on error.
    try:
        return r.json()
    except Exception:
        try:
            # Some wrappers expose .text or .content
            txt = getattr(r, 'text', None)
            if txt:
                return json.loads(txt)
        except Exception:
            pass
        return {}


def get_updates_real(base_url, tok, buf="", to=None):
    if to is None:
        to = DEFAULT_POLL_SEC
    try:
        return ilink_post(
            base_url,
            "ilink/bot/getupdates",
            {"get_updates_buf": buf, "base_info": {"channel_version": ILINK_VER}},
            tok,
            to + 5,  # HTTP timeout slightly longer than iLink long-poll
        )
    except requests.exceptions.Timeout:
        return {"ret": 0, "msgs": [], "get_updates_buf": buf}
    except Exception as e:
        log.warning("getUpdates: %s", e)
        return {"ret": -1, "msgs": [], "get_updates_buf": buf}


def send_text_ilink(base_url, tok, to_user, text, ctx=None):
    """Send a plain text reply through iLink."""
    m = {
        "from_user_id": "",
        "to_user_id": to_user,
        "client_id": "hc-" + secrets.token_hex(8),
        "message_type": 2,
        "message_state": 2,
        "item_list": [{"type": T, "text_item": {"text": text}}],
    }
    if ctx:
        m["context_token"] = ctx
    return ilink_post(
        base_url,
        "ilink/bot/sendmessage",
        {"msg": m, "base_info": {"channel_version": ILINK_VER}},
        tok,
    )


# ---------------------------------------------------------------------------
# Message queue with event-based long-poll
# ---------------------------------------------------------------------------


class MessageQueue:
    """Thread-safe queue with blocking dequeue for long-poll simulation.

    Uses collections.deque for O(1) popleft when capacity is exceeded. When the
    queue is full the oldest message is discarded before adding the newest,
    and a warning is logged.
    """

    def __init__(self, capacity=QUEUE_CAP):
        self.lock = threading.Lock()
        self.event = threading.Event()
        # use deque for efficient pops from left
        self.msgs = deque()
        self.capacity = int(capacity)

    def enqueue(self, msg):
        with self.lock:
            if len(self.msgs) >= self.capacity:
                # drop oldest explicitly so we can log it
                try:
                    self.msgs.popleft()
                except Exception:
                    pass
                log.warning("Queue full (%d), dropped oldest", self.capacity)
            self.msgs.append(msg)
            # always signal waiting long-poll clients
            self.event.set()

    def dequeue_all(self, timeout=None):
        """Return all queued messages, blocking up to *timeout* seconds."""
        if timeout is not None and timeout > 0:
            self.event.wait(timeout=timeout)
        with self.lock:
            batch = list(self.msgs)
            # clear deque
            try:
                self.msgs.clear()
            except Exception:
                self.msgs = deque()
            self.event.clear()
        return batch

    def size(self):
        with self.lock:
            return len(self.msgs)


# ---------------------------------------------------------------------------
# Gateway proxy handler
# ---------------------------------------------------------------------------

PROXY_ALLOWLIST = frozenset([
    "ilink/bot/getupdates",
    "ilink/bot/sendmessage",
    "ilink/bot/getuploadurl",
    "ilink/bot/sendtyping",
    "ilink/bot/getconfig",
    "ilink/bot/get_bot_qrcode",
    "ilink/bot/get_qrcode_status",
])


def make_proxy_handler(queue, ilink_base_url, ilink_token, state, tag):
    """Factory: create a request handler class bound to a specific queue.

    *state* -- State instance for route lookup.
    *tag*   -- e.g. "[Hermes Agent]"; prepended to text items in
    sendmessage when the destination user's route is /both.
    """

    class GatewayProxyHandler(BaseHTTPRequestHandler):

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            ep = urlparse(self.path).path.lstrip("/")

            if ep not in PROXY_ALLOWLIST:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"ret":-1,"errmsg":"not allowed"}')
                log.warning("Proxy blocked: %s", ep)
                return

            try:
                if ep == "ilink/bot/getupdates":
                    self._handle_getupdates(body)
                elif ep == "ilink/bot/sendmessage":
                    self._handle_sendmessage(body)
                else:
                    self._proxy_passthrough(body)
            except BrokenPipeError:
                log.debug("Client disconnected (BrokenPipeError)")
            except ConnectionResetError:
                log.debug("Client disconnected (ConnectionResetError)")

        # -- getupdates: return from queue with long-poll ----------------

        def _handle_getupdates(self, body):
            try:
                bd = json.loads(body) if body else {}
            except Exception:
                bd = {}
            client_buf = bd.get("get_updates_buf", "")

            msgs = queue.dequeue_all(timeout=DEFAULT_POLL_SEC)
            resp = {"ret": 0, "msgs": msgs, "get_updates_buf": client_buf}
            self._write_json(200, resp)
            if msgs:
                log.info("Proxy [%s] getupdates -> %d msgs", tag or "?", len(msgs))

        # -- sendmessage: forward to real iLink with text tagging --------

        def _handle_sendmessage(self, body):
            try:
                bd = json.loads(body) if body else {}
            except Exception:
                bd = {}

            # Prefix every outbound text message with this proxy's tag.
            msg_obj = bd.get("msg", {})
            if tag:
                for item in msg_obj.get("item_list", []):
                    if item.get("type") == T:
                        ti = item.get("text_item", {})
                        original = ti.get("text", "")
                        if original:
                            ti["text"] = f"{tag} {original}"

            self._forward_to_ilink(
                "ilink/bot/sendmessage",
                json.dumps(bd).encode() if bd else body,
            )

        # -- other endpoints: passthrough to real iLink ------------------

        def _proxy_passthrough(self, body):
            ep = urlparse(self.path).path.lstrip("/")
            self._forward_to_ilink(ep, body)

        def _forward_to_ilink(self, ep, body):
            url = ilink_base_url.rstrip("/") + "/" + ep
            try:
                resp = requests.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "AuthorizationType": "ilink_bot_token",
                        "iLink-App-Id": "",
                        "iLink-App-ClientVersion": ILINK_CV,
                        "Authorization": "Bearer " + ilink_token,
                    },
                    data=body,
                    timeout=30,
                )
                self.send_response(resp.status_code)
                for k, v in resp.headers.items():
                    if k.lower() not in (
                        "transfer-encoding", "content-encoding", "connection",
                    ):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.content)
            except BrokenPipeError:
                # Gateway disconnected before we could write back the response.
                # The upstream request already succeeded; nothing to retry.
                log.debug("BrokenPipe on write-back (benign): %s", ep)
            except Exception as e:
                log.error("Proxy forward error: %s", e)
                try:
                    err = json.dumps({"ret": -1, "errmsg": str(e)}).encode()
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err)))
                    self.end_headers()
                    self.wfile.write(err)
                except BrokenPipeError:
                    log.debug("BrokenPipe writing error response (benign)")


        def _write_json(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass  # Suppress default HTTP log noise.

    return GatewayProxyHandler


# ---------------------------------------------------------------------------
# Message routing
# ---------------------------------------------------------------------------


def route_message(
    uid,
    msg,
    route,
    hermes_q=None,
    openclaw_q=None,
    agent_queues=None,
    registry=None,
):
    """Enqueue *msg* to the correct proxy queue(s) based on *route*."""
    if agent_queues is None:
        agent_queues = {}
        if hermes_q is not None:
            agent_queues["hermes"] = hermes_q
        if openclaw_q is not None:
            agent_queues["openclaw"] = openclaw_q

    reg = registry or _registry_from_legacy_queues(
        hermes_q=agent_queues.get("hermes"),
        openclaw_q=agent_queues.get("openclaw"),
    )
    targets = reg.route_targets(route)
    if not targets:
        log.warning("No route targets resolved for %s route=%r", uid[:16], route)
        return

    for target in targets:
        q = agent_queues.get(target)
        if q is None:
            log.warning("Queue not available for %s target=%s", uid[:16], target)
            continue
        q.enqueue(msg)


def _process_aliases_data(data):
    """Normalize a raw aliases dict -> {key: [alias, ...], '_groups': {...}}."""
    out = {}
    groups = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if str(k).lower() == "_groups" and isinstance(v, dict):
                for gk, gv in v.items():
                    gname = _coerce_name(gk)
                    if not gname or not isinstance(gv, list):
                        continue
                    members = []
                    for m in gv:
                        mn = _coerce_name(m)
                        if mn and mn not in members:
                            members.append(mn)
                    if members:
                        groups[gname] = members
                continue
            if not isinstance(v, list):
                continue
            normalized = []
            for a in v:
                if not isinstance(a, str):
                    continue
                a2 = a.lstrip("@").strip().lower()
                if a2:
                    normalized.append(a2)
            if normalized:
                out[k.lower()] = normalized
    if groups:
        out["_groups"] = groups
    return out


def load_aliases(path_or_dict):
    """Load mention aliases from a file path OR an inline dict.

    When given a dict (e.g. from agents.json 'mention_aliases' field), it is
    processed directly without any file I/O or caching.

    When given a path string, the file is read with simple mtime caching.

    Returns a dict mapping lowercase route-key -> list of normalized aliases
    (each alias lowercased and with any leading '@' stripped).
    """
    if isinstance(path_or_dict, dict):
        return _process_aliases_data(path_or_dict)

    path = path_or_dict
    try:
        p = Path(path)
        mtime = p.stat().st_mtime
    except Exception:
        log.debug("Aliases file missing or inaccessible: %s", path)
        return {}
    cache = getattr(load_aliases, "_cache", None)
    if cache and cache.get("path") == str(p) and cache.get("mtime") == mtime:
        return cache.get("aliases", {})
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        out = _process_aliases_data(data)
        load_aliases._cache = {"path": str(p), "mtime": mtime, "aliases": out}
        return out
    except Exception as e:
        log.warning("Failed to load aliases %s: %s", path, e)
        return {}


def find_route_from_mention(text, aliases, registry=None):
    """Return (route_spec, matched_alias) if *text* contains an @alias.

    To avoid short-alias prefix matches (e.g. @h matching @hemers), we
    flatten aliases and check longer aliases first. Matches require the alias
    to be followed by end-of-string or a non-word char (so @claw今天天气 still
    matches, while @h in @hemers does not).

    Returns:
      (route_spec, alias_str) on match, or None when no match.
    """
    if not text or not aliases:
        return None

    groups = aliases.get("_groups", {}) if isinstance(aliases, dict) else {}
    flat = []
    for key, vals in aliases.items():
        if key == "_groups":
            continue
        for a in vals:
            flat.append((key, a))
    # prefer longer alias strings first
    flat.sort(key=lambda t: -len(t[1]))
    for key, a in flat:
        # match @alias optionally followed by ':' and then end or a non-word char
        pat = r"@" + re.escape(a) + r"(?:\:)?(?=$|[^0-9A-Za-z_])"
        if re.search(pat, text, flags=re.IGNORECASE):
            k = key.lower()
            if k in ("both", "all"):
                return ("both", a)
            if registry and registry.is_valid_agent(k):
                return (k, a)
            if k in groups:
                return ({"group": k}, a)
            return (k, a)

    for gname in groups:
        pat = r"@" + re.escape(gname) + r"(?:\:)?(?=$|[^0-9A-Za-z_])"
        if re.search(pat, text, flags=re.IGNORECASE):
            return ({"group": gname}, gname)

    return None


def proc_msg(
    msg,
    state,
    base_url,
    token,
    hermes_q=None,
    openclaw_q=None,
    agent_queues=None,
    registry=None,
):
    """Process one inbound iLink message.

    Supports per-message @mentions to temporarily override routing. Aliases
    are loaded from a JSON file specified by the MENTION_ALIASES_FILE env var
    (defaults to ./mention_aliases.json). Slash commands (/whoami, /hermes,
    /openclaw, /both) are intercepted and not forwarded.
    """
    import json as _json

    uid = msg.get("from_user_id", "")
    ctx = msg.get("context_token", "")
    items = msg.get("item_list", [])
    if msg.get("message_type", 1) != 1:
        return
    log.info("Msg from=%s... items=%d", uid[:16], len(items))

    txt = extract_text(items)
    has_any = txt or any(it.get("type", 0) != 0 for it in items)
    if not has_any:
        return

    if agent_queues is None:
        agent_queues = {}
        if hermes_q is not None:
            agent_queues["hermes"] = hermes_q
        if openclaw_q is not None:
            agent_queues["openclaw"] = openclaw_q

    reg = registry or _registry_from_legacy_queues(
        hermes_q=agent_queues.get("hermes"),
        openclaw_q=agent_queues.get("openclaw"),
    )

    # Show status on first contact.
    if state.should_show_status(uid) and not txt.startswith("/"):
        reply = cmd(state, uid, "/whoami", registry=reg)
        send_text_ilink(base_url, token, uid, reply, ctx)
        state.mark_status_shown(uid)

    # Slash commands are text-only; never forwarded to gateways.
    if txt.startswith("/"):
        r = cmd(state, uid, txt, registry=reg)
        if r:
            send_text_ilink(base_url, token, uid, r, ctx)
            return

    # Load aliases: prefer inline dict from agents.json, else fall back to file.
    if reg.aliases_raw is not None:
        aliases = load_aliases(reg.aliases_raw)
    else:
        aliases_path = os.getenv(
            "MENTION_ALIASES_FILE",
            str(Path(__file__).parent / "mention_aliases.json"),
        )
        aliases = load_aliases(aliases_path)

    # Check for an @ mention that maps to a specific route.  If present,
    # override routing for this single message only and strip the mention
    # text before forwarding.
    found = find_route_from_mention(txt, aliases, registry=reg)
    if found:
        if not isinstance(found, (tuple, list)) or len(found) < 2:
            log.error("find_route_from_mention returned unexpected value: %r", found)
            return
        mention_route, matched_alias = found[0], found[1]
        try:
            mod_msg = _json.loads(_json.dumps(msg))  # deep copy
            # Use the SAME matching rule to remove exactly the matched alias.
            pat = r"@" + re.escape(matched_alias) + r"(?:\:)?(?=$|[^0-9A-Za-z_])"
            for it in mod_msg.get("item_list", []):
                if it.get("type") == T:
                    ti = it.get("text_item", {})
                    if ti.get("text"):
                        ti["text"] = re.sub(pat, "", ti["text"], flags=re.IGNORECASE).strip()
            route_message(
                uid,
                mod_msg,
                mention_route,
                agent_queues=agent_queues,
                registry=reg,
            )
        except Exception as e:
            log.error("Error handling mention routing: %s", e, exc_info=True)
        return

    # Default persistent routing
    route = state.get(uid, default_route=reg.default_route)
    route_message(uid, msg, route, agent_queues=agent_queues, registry=reg)


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

MAX_FAILS = 3
BACKOFF = 30


def poll_loop(
    base_url,
    token,
    state,
    hermes_q=None,
    openclaw_q=None,
    poll_sec=None,
    agent_queues=None,
    registry=None,
):
    if poll_sec is None:
        poll_sec = DEFAULT_POLL_SEC
    buf = ""
    fails = 0
    while True:
        try:
            resp = get_updates_real(base_url, token, buf, poll_sec)
            ret, ec = resp.get("ret"), resp.get("errcode")
            if ret not in (0, None) or ec not in (0, None):
                log.warning("getUpdates err: ret=%s ec=%s", ret, ec)
                fails += 1
                if fails >= MAX_FAILS:
                    time.sleep(BACKOFF)
                    fails = 0
                else:
                    time.sleep(2)
                continue
            fails = 0
            if resp.get("get_updates_buf"):
                buf = resp["get_updates_buf"]
            for m in resp.get("msgs", []):
                try:
                    proc_msg(
                        m,
                        state,
                        base_url,
                        token,
                        hermes_q=hermes_q,
                        openclaw_q=openclaw_q,
                        agent_queues=agent_queues,
                        registry=registry,
                    )
                except Exception as e:
                    log.error("proc: %s", e, exc_info=True)
        except Exception as e:
            log.error("Loop: %s", e, exc_info=True)
            fails += 1
            if fails >= MAX_FAILS:
                time.sleep(BACKOFF)
                fails = 0
            else:
                time.sleep(2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env")
    except Exception:
        # python-dotenv not available — fall back to a lightweight .env loader
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            try:
                with env_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        # don't overwrite existing environment variables
                        if k and k not in os.environ:
                            os.environ[k] = v
                log.info("Loaded .env fallback from %s", env_path)
            except Exception:
                log.warning("Failed to parse .env fallback; continuing with existing env vars")
        else:
            log.warning("python-dotenv not available and .env not found; skipping .env load")

    try:
        config = load_agents_config()
    except Exception as e:
        log.error("Failed to load AGENTS config: %s", e)
        sys.exit(1)

    base_url = (
        str(config.get("ilink_base_url", "")).strip()
        or os.getenv("ILINK_BASE_URL", "https://ilinkai.weixin.qq.com")
    )
    token = str(config.get("ilink_token", "")).strip() or os.getenv("ILINK_TOKEN", "")

    base_dir = Path(__file__).parent
    logs_dir = base_dir / "logs"
    default_state = logs_dir / "router_state.json"
    default_log = logs_dir / "hermesclaw.log"

    # STATE_FILE / LOG_FILE support relative paths against script directory.
    state_file_raw = os.getenv("STATE_FILE", "").strip()
    log_file_raw = os.getenv("LOG_FILE", "").strip()
    state_file = str(_resolve_path(state_file_raw, base_dir)) if state_file_raw else str(default_state)
    log_file = str(_resolve_path(log_file_raw, base_dir)) if log_file_raw else str(default_log)
    poll_sec = int(os.getenv("LONG_POLL_TIMEOUT", str(DEFAULT_POLL_SEC)))

    # Ensure parent directories for state and log exist. This prevents FileNotFoundError
    try:
        state_parent = Path(state_file).expanduser().resolve().parent
        state_parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # best-effort; continue and let the application log if it still fails
        pass
    try:
        log_parent = Path(log_file).expanduser().resolve().parent
        log_parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    handlers = [logging.FileHandler(log_file, encoding="utf-8")]
    # Avoid duplicate log lines when manager redirects stdout/stderr to log_file.
    if getattr(sys.stdout, "isatty", lambda: False)():
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )

    if not token:
        log.error("No ILINK_TOKEN set!")
        sys.exit(1)

    registry = AgentRegistry(config)
    state = State(state_file, default_route=registry.default_route)
    agent_queues = {}

    log.info("=" * 60)
    log.info("HermesClaw v2 -- multi gateway proxy")
    log.info("iLink: %s", base_url)
    log.info("Config source: %s", config.get("config_source", "unknown"))
    for agent in registry.enabled_agents.values():
        log.info(
            "Agent %s proxy: %s:%d (tag=%s)",
            agent["name"],
            agent["host"],
            agent["port"],
            agent["tag"],
        )
    log.info("Default route: %s", registry.default_route)
    log.info("Groups: %s", ", ".join(sorted(registry.groups.keys())))
    log.info("=" * 60)

    servers = []

    for agent in registry.enabled_agents.values():
        q = MessageQueue()
        handler = make_proxy_handler(
            q,
            base_url,
            token,
            state,
            tag=agent.get("tag", f"[{agent['name']}]")
        )
        srv = ThreadingHTTPServer((agent["host"], int(agent["port"])), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        agent_queues[agent["name"]] = q
        log.info(
            "Agent %s proxy started on %s:%d",
            agent["name"],
            agent["host"],
            int(agent["port"]),
        )

    poll_thread = threading.Thread(
        target=poll_loop,
        args=(base_url, token, state, None, None, poll_sec, agent_queues, registry),
        daemon=True,
    )
    poll_thread.start()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda s, f: stop.set())
    signal.signal(signal.SIGTERM, lambda s, f: stop.set())

    while not stop.is_set():
        time.sleep(1)

    log.info("Shutting down...")
    for s in servers:
        s.shutdown()
    log.info("Stopped")


if __name__ == "__main__":
    main()
