#!/usr/bin/env python3
"""
Watch Bilibili UP users' dynamic posts and send new items to a Feishu group bot.

Requires:
  pip install requests

Run:
  python bilibili_feishu_watcher.py --config config.json
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import hmac
import json
import logging
import os
import re
import signal
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


BILIBILI_DYNAMIC_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
BILIBILI_DYNAMIC_DETAIL_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail"
BILIBILI_LEGACY_DYNAMIC_DETAIL_URL = "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail"
BILIBILI_OPUS_URL = "https://www.bilibili.com/opus/{dynamic_id}"
BILIBILI_INITIAL_STATE_RE = re.compile(r"window\.__INITIAL_STATE__=(.*?);\(function\(\)", re.S)
BILIBILI_DYNAMIC_FEATURES = (
    "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote,decorationCard,"
    "forwardListHidden,ugcDelete,onlyfansQaCard"
)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_OPUS_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0"
MAX_SEEN_IDS_PER_USER = 200
BILIBILI_READ_STATE_COOKIE_PREFIXES = (
    "bp_t_offset_",
)
BILIBILI_READ_STATE_COOKIE_NAMES = {
    "hit-dyn-v2",
}


@dataclass(frozen=True)
class UpUser:
    uid: str
    name: str | None = None


class WatcherError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WatcherError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WatcherError(f"Invalid JSON in {path}: {exc}") from exc


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def strip_bilibili_read_state_cookies(cookie: str | None) -> str | None:
    if not cookie:
        return cookie

    kept_parts: list[str] = []
    removed_names: list[str] = []
    for part in cookie.split(";"):
        part = part.strip()
        if not part:
            continue
        name = part.split("=", 1)[0].strip()
        should_remove = name in BILIBILI_READ_STATE_COOKIE_NAMES or any(
            name.startswith(prefix) for prefix in BILIBILI_READ_STATE_COOKIE_PREFIXES
        )
        if should_remove:
            removed_names.append(name)
            continue
        kept_parts.append(part)

    if removed_names:
        logging.debug("Stripped Bilibili read-state cookies for feed request: %s", ", ".join(removed_names))

    return "; ".join(kept_parts)


def normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    last_ids = state.setdefault("last_dynamic_ids", {})
    if not isinstance(last_ids, dict):
        state["last_dynamic_ids"] = {}

    seen_ids = state.setdefault("seen_dynamic_ids", {})
    if not isinstance(seen_ids, dict):
        state["seen_dynamic_ids"] = {}

    return state


def remember_seen(state: dict[str, Any], uid: str, dynamic_ids: list[str]) -> None:
    seen_ids = state.setdefault("seen_dynamic_ids", {})
    existing = seen_ids.get(uid, [])
    if not isinstance(existing, list):
        existing = []

    merged: list[str] = []
    for dynamic_id in [*dynamic_ids, *[str(item) for item in existing]]:
        if dynamic_id and dynamic_id not in merged:
            merged.append(dynamic_id)

    seen_ids[uid] = merged[:MAX_SEEN_IDS_PER_USER]


@contextmanager
def single_instance_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+", encoding="utf-8")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
            except OSError as exc:
                raise WatcherError(
                    f"Another watcher instance is already running; lock file is busy: {lock_path}"
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                raise WatcherError(
                    f"Another watcher instance is already running; lock file is busy: {lock_path}"
                ) from exc

        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"pid={os.getpid()}\n")
        lock_file.flush()
        yield
    finally:
        try:
            if not locked:
                return
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def normalize_up_users(raw: list[Any]) -> list[UpUser]:
    users: list[UpUser] = []
    for item in raw:
        if isinstance(item, dict):
            uid = str(item.get("uid", "")).strip()
            name = item.get("name")
        else:
            uid = str(item).strip()
            name = None
        if not uid:
            raise WatcherError("Each up_users item must include a uid")
        users.append(UpUser(uid=uid, name=str(name).strip() if name else None))
    if not users:
        raise WatcherError("Config field up_users cannot be empty")
    return users


def parse_dynamic_id(value: str) -> str:
    value = value.strip()
    if not value:
        raise WatcherError("Dynamic URL or id cannot be empty")

    if value.isdigit():
        return value

    parsed = urlparse(value)
    path = parsed.path if parsed.scheme else value
    match = re.search(r"/(?:opus|dynamic|t)/(\d+)", path)
    if match:
        return match.group(1)

    match = re.search(r"\b(\d{12,})\b", value)
    if match:
        return match.group(1)

    raise WatcherError(f"Cannot parse dynamic id from: {value}")


def import_bilibili_cookie_from_har(config_path: Path, har_path: Path) -> None:
    config = load_json(config_path)
    har = load_json(har_path)
    entries = har.get("log", {}).get("entries", [])
    if not isinstance(entries, list):
        raise WatcherError(f"Invalid HAR file: {har_path}")

    selected_cookie = ""
    selected_url = ""
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request") if isinstance(entry.get("request"), dict) else {}
        url = str(request.get("url") or "")
        if "www.bilibili.com/opus/" not in url:
            continue
        headers = request.get("headers") if isinstance(request.get("headers"), list) else []
        for header in headers:
            if not isinstance(header, dict):
                continue
            if str(header.get("name") or "").lower() == "cookie":
                selected_cookie = str(header.get("value") or "")
                selected_url = url
                break
        if selected_cookie:
            break

    if not selected_cookie:
        raise WatcherError(f"No www.bilibili.com/opus Cookie header found in HAR: {har_path}")

    config["bilibili_cookie"] = selected_cookie
    config["prefer_bilibili_cookie"] = True
    save_json(config_path, config)
    logging.info("Imported Bilibili cookie from HAR request: %s", selected_url)


def feishu_signature(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def post_to_feishu(webhook: str, title: str, text: str, secret: str | None = None) -> None:
    payload: dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": [
                {"tag": "markdown", "content": text},
            ],
        },
    }

    if secret:
        timestamp = int(time.time())
        payload["timestamp"] = str(timestamp)
        payload["sign"] = feishu_signature(secret, timestamp)

    response = requests.post(webhook, json=payload, timeout=15)
    response.raise_for_status()
    result = response.json()
    if result.get("code") not in (0, None):
        if result.get("code") == 19024:
            raise WatcherError(
                "Feishu webhook keyword check failed. "
                "Set config field feishu_keyword to the keyword configured in the Feishu bot security settings."
            )
        raise WatcherError(f"Feishu webhook returned error: {result}")


def bilibili_headers(uid: str, cookie: str | None) -> dict[str, str]:
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": f"https://space.bilibili.com/{uid}/dynamic",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Connection": "close",
        "Origin": "https://space.bilibili.com",
        "Pragma": "no-cache",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def request_bilibili_json(
    url: str,
    params: dict[str, str],
    uid: str,
    cookie: str | None,
    label: str,
    prefer_cookie: bool = False,
) -> dict[str, Any]:
    attempts = 3
    if prefer_cookie and cookie:
        cookie_options = [cookie, None]
    else:
        cookie_options = [None]
    if cookie and cookie not in cookie_options:
        cookie_options.append(cookie)

    last_error: Exception | None = None
    for cookie_option in cookie_options:
        if cookie_option is not None and not prefer_cookie:
            logging.warning("Retrying Bilibili %s with cookie after anonymous request errors", label)
        elif cookie_option is None and prefer_cookie and cookie:
            logging.warning("Retrying Bilibili %s without cookie after cookie request errors", label)

        for attempt in range(1, attempts + 1):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=bilibili_headers(uid, cookie_option),
                    timeout=(8, 20),
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt < attempts:
                    logging.warning(
                        "Bilibili %s request failed, retrying %s/%s: %s",
                        label,
                        attempt,
                        attempts,
                        exc,
                    )
                    time.sleep(attempt * 2)
                    continue
                break
            except ValueError as exc:
                raise WatcherError(f"Bilibili {label} API returned invalid JSON") from exc

    raise WatcherError(
        f"Failed to connect to Bilibili {label} API after retries: {last_error}"
    ) from last_error


def request_dynamics(
    uid: str,
    cookie: str | None,
    prefer_cookie: bool = False,
    strip_read_state: bool = True,
) -> list[dict[str, Any]]:
    feed_cookie = strip_bilibili_read_state_cookies(cookie) if strip_read_state else cookie
    payload = request_bilibili_json(
        BILIBILI_DYNAMIC_URL,
        {
            "host_mid": uid,
            "timezone_offset": "-480",
            "features": BILIBILI_DYNAMIC_FEATURES,
        },
        uid,
        feed_cookie,
        "feed",
        prefer_cookie=prefer_cookie,
    )
    if payload.get("code") != 0:
        raise WatcherError(f"Bilibili API returned error for uid={uid}: {payload}")

    items = payload.get("data", {}).get("items", [])
    return items if isinstance(items, list) else []


def request_dynamic_detail(dynamic_id: str, uid: str, cookie: str | None) -> dict[str, Any] | None:
    payload = request_bilibili_json(
        BILIBILI_DYNAMIC_DETAIL_URL,
        {
            "id": dynamic_id,
            "timezone_offset": "-480",
            "features": BILIBILI_DYNAMIC_FEATURES,
        },
        uid,
        cookie,
        "detail",
        prefer_cookie=True,
    )
    if payload.get("code") != 0:
        raise WatcherError(f"Bilibili detail API returned error for dynamic_id={dynamic_id}: {payload}")

    item = payload.get("data", {}).get("item")
    return item if isinstance(item, dict) else None


def request_legacy_dynamic_detail(dynamic_id: str, uid: str, cookie: str | None) -> dict[str, Any] | None:
    payload = request_bilibili_json(
        BILIBILI_LEGACY_DYNAMIC_DETAIL_URL,
        {"dynamic_id": dynamic_id},
        uid,
        cookie,
        "legacy detail",
        prefer_cookie=True,
    )
    if payload.get("code") != 0:
        raise WatcherError(f"Bilibili legacy detail API returned error for dynamic_id={dynamic_id}: {payload}")

    card = payload.get("data", {}).get("card")
    if not isinstance(card, dict):
        return None

    parsed_card = card.get("card")
    if isinstance(parsed_card, str):
        try:
            card["card"] = json.loads(parsed_card)
        except json.JSONDecodeError:
            pass

    return card


def request_opus_initial_state(dynamic_id: str, uid: str, cookie: str | None) -> dict[str, Any] | None:
    url = BILIBILI_OPUS_URL.format(dynamic_id=dynamic_id)
    attempts = 3
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            headers = bilibili_headers(uid, cookie)
            headers["User-Agent"] = DEFAULT_OPUS_USER_AGENT
            headers["Referer"] = "https://t.bilibili.com/?spm_id_from=333.1007.0.0"
            headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            headers["Accept-Language"] = "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2"
            headers["Upgrade-Insecure-Requests"] = "1"
            headers["Sec-Fetch-Dest"] = "document"
            headers["Sec-Fetch-Mode"] = "navigate"
            headers["Sec-Fetch-Site"] = "same-site"
            headers["Sec-Fetch-User"] = "?1"
            headers["DNT"] = "1"
            headers["Sec-GPC"] = "1"
            headers["Priority"] = "u=0, i"
            response = requests.get(url, headers=headers, timeout=(8, 20))
            response.raise_for_status()
            match = BILIBILI_INITIAL_STATE_RE.search(response.text)
            if not match:
                return None
            state = json.loads(match.group(1))
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                log_opus_initial_state_summary(dynamic_id, state)
            return state
        except (requests.exceptions.RequestException, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < attempts:
                logging.warning(
                    "Bilibili opus HTML request failed, retrying %s/%s: %s",
                    attempt,
                    attempts,
                    exc,
                )
                time.sleep(attempt * 2)
                continue
            break

    logging.warning("Failed to fetch Bilibili opus HTML after retries: %s", last_error)
    return None


def log_opus_initial_state_summary(dynamic_id: str, state: dict[str, Any]) -> None:
    detail = state.get("detail") if isinstance(state, dict) else None
    if not isinstance(detail, dict):
        logging.debug("Opus HTML %s initial state has no detail object", dynamic_id)
        return

    basic = detail.get("basic") if isinstance(detail.get("basic"), dict) else {}
    modules = detail.get("modules") if isinstance(detail.get("modules"), list) else []
    module_types = [
        str(module.get("module_type"))
        for module in modules
        if isinstance(module, dict) and module.get("module_type")
    ]
    content_chars = len(extract_opus_modules_text(modules))
    logging.debug(
        "Opus HTML %s initial state: is_only_fans=%s modules=%s content_chars=%s",
        dynamic_id,
        basic.get("is_only_fans"),
        ",".join(module_types),
        content_chars,
    )


def extract_dynamic(dynamic: dict[str, Any], fallback_uid: str, fallback_name: str | None) -> dict[str, str]:
    modules = dynamic.get("modules", {})
    author: dict[str, Any] = {}
    dynamic_module: dict[str, Any] = {}
    if isinstance(modules, dict):
        author = modules.get("module_author", {})
        dynamic_module = modules.get("module_dynamic", {})
    elif isinstance(modules, list):
        for module in modules:
            if not isinstance(module, dict):
                continue
            if module.get("module_author"):
                author = module.get("module_author") or {}
            if module.get("module_content"):
                dynamic_module = module
    desc = dynamic_module.get("desc") or {}
    major = dynamic_module.get("major") or {}

    dynamic_id = str(dynamic.get("id_str") or dynamic.get("id") or "")
    basic = dynamic.get("basic") if isinstance(dynamic.get("basic"), dict) else {}
    author_name = str(author.get("name") or fallback_name or basic.get("uid") or fallback_uid)
    pub_time = str(author.get("pub_time") or author.get("pub_ts") or "")

    text = extract_dynamic_text(dynamic)
    major_type = str(major.get("type") or "").strip()
    access_hint = detect_access_hint(dynamic)

    url = f"https://t.bilibili.com/{dynamic_id}" if dynamic_id else f"https://space.bilibili.com/{fallback_uid}/dynamic"

    text_is_fallback = False
    if not text:
        restricted_text = summarize_restricted_dynamic(dynamic, major)
        if restricted_text:
            text = restricted_text
        else:
            text_is_fallback = True
            text = summarize_major(major, major_type)

    return {
        "id": dynamic_id,
        "author": author_name,
        "pub_time": pub_time,
        "type": major_type or "dynamic",
        "access_hint": access_hint,
        "text": text or "(无文字内容)",
        "_text_is_fallback": "1" if text_is_fallback else "0",
        "url": url,
    }


def dynamic_id_int(dynamic: dict[str, Any]) -> int:
    dynamic_id = str(dynamic.get("id_str") or dynamic.get("id") or "")
    return int(dynamic_id) if dynamic_id.isdigit() else 0


def is_pinned_dynamic(dynamic: dict[str, Any]) -> bool:
    modules = dynamic.get("modules", {})
    tag: Any = None
    if isinstance(modules, dict):
        tag = modules.get("module_tag")
    elif isinstance(modules, list):
        for module in modules:
            if isinstance(module, dict) and module.get("module_tag"):
                tag = module.get("module_tag")
                break

    if not isinstance(tag, dict):
        return False

    return str(tag.get("text") or "").strip().lower() in {"置顶", "pinned", "top"}


def sorted_feed_dynamics(dynamics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [dynamic for dynamic in dynamics if dynamic_id_int(dynamic) > 0],
        key=dynamic_id_int,
    )


def summarize_restricted_dynamic(dynamic: dict[str, Any], major: dict[str, Any]) -> str:
    basic = dynamic.get("basic") if isinstance(dynamic.get("basic"), dict) else {}
    blocked = major.get("blocked") if isinstance(major.get("blocked"), dict) else {}
    if not blocked and not basic.get("is_only_fans"):
        return ""

    parts = ["充电专属动态（当前 Cookie 未解锁正文）"]
    hint = str(blocked.get("hint_message") or "").strip()
    if hint:
        parts.append(hint)

    button = blocked.get("button") if isinstance(blocked.get("button"), dict) else {}
    button_text = str(button.get("text") or "").strip()
    if button_text:
        parts.append(f"解锁方式：{button_text}")

    return "\n".join(parts)


def fill_dynamic_detail_text(
    item: dict[str, str],
    uid: str,
    fallback_name: str | None,
    cookie: str | None,
    source_dynamic: dict[str, Any] | None = None,
) -> dict[str, str]:
    needs_detail = (
        item.get("_text_is_fallback") == "1"
        or bool(item.get("access_hint"))
        or is_restricted_placeholder_text(item.get("text", ""))
    )
    if not needs_detail or not item.get("id"):
        return item

    detail: dict[str, Any] | None = None
    legacy_detail: dict[str, Any] | None = None

    try:
        detail = request_dynamic_detail(item["id"], uid, cookie)
    except Exception as exc:
        logging.warning("Failed to fetch dynamic detail %s: %s", item["id"], exc)

    if detail:
        detail_item = extract_dynamic(detail, uid, fallback_name)
        if (
            (
                detail_item.get("_text_is_fallback") != "1"
                or item.get("_text_is_fallback") != "1"
            )
            and detail_item.get("text")
            and not is_module_summary_text(detail_item["text"])
            and not is_restricted_placeholder_text(detail_item["text"])
            and not is_page_title_text(detail_item["text"])
        ):
            logging.debug("Filled dynamic %s text from detail API", item["id"])
            item["text"] = detail_item["text"]
            item["_text_is_fallback"] = "0"
            if detail_item.get("access_hint"):
                item["access_hint"] = detail_item["access_hint"]
            if detail_item.get("type"):
                item["type"] = detail_item["type"]
            return item
        if detail_item.get("text"):
            logging.debug("Ignored dynamic %s module summary from detail API: %s", item["id"], detail_item["text"])

    html_state = request_opus_initial_state(item["id"], uid, cookie)
    html_detail = html_state.get("detail") if isinstance(html_state, dict) else None
    if isinstance(html_detail, dict):
        html_item = extract_dynamic(html_detail, uid, fallback_name)
        if (
            html_item.get("text")
            and not is_module_summary_text(html_item["text"])
            and not is_restricted_placeholder_text(html_item["text"])
            and not is_page_title_text(html_item["text"])
        ):
            logging.debug("Filled dynamic %s text from opus HTML initial state", item["id"])
            item["text"] = html_item["text"]
            item["_text_is_fallback"] = "0"
            if html_item.get("access_hint"):
                item["access_hint"] = html_item["access_hint"]
            if html_item.get("type"):
                item["type"] = html_item["type"]
            if html_item.get("pub_time"):
                item["pub_time"] = html_item["pub_time"]
            return item

    try:
        legacy_detail = request_legacy_dynamic_detail(item["id"], uid, cookie)
    except Exception as exc:
        logging.warning("Failed to fetch legacy dynamic detail %s: %s", item["id"], exc)

    if legacy_detail:
        legacy_text = extract_dynamic_text(legacy_detail)
        if (
            legacy_text
            and not is_module_summary_text(legacy_text)
            and not is_restricted_placeholder_text(legacy_text)
            and not is_page_title_text(legacy_text)
        ):
            logging.debug("Filled dynamic %s text from legacy detail API", item["id"])
            item["text"] = legacy_text
            item["_text_is_fallback"] = "0"
            return item

    dump_dynamic_debug(
        item["id"],
        {"feed": source_dynamic, "detail": detail, "html_state": html_state, "legacy_detail": legacy_detail},
    )
    return item


def detect_access_hint(dynamic: dict[str, Any]) -> str:
    hints: list[str] = []
    key_markers = ("onlyfans", "only_fans", "charge", "paid", "pay", "vip")
    text_markers = (
        "充电",
        "充电专属",
        "包月",
        "专属动态",
        "开通",
        "付费",
        "onlyfans",
        "only_fans",
    )

    def add_hint(hint: str) -> None:
        if hint not in hints:
            hints.append(hint)

    def walk(value: Any, key: str = "") -> None:
        lowered_key = key.lower()
        if any(marker in lowered_key for marker in key_markers):
            add_hint(key)

        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, str(child_key))
            return
        if isinstance(value, list):
            for child in value:
                walk(child, key)
            return
        if isinstance(value, str):
            lowered_value = value.lower()
            if any(marker in value or marker in lowered_value for marker in text_markers):
                add_hint(value[:80])
            return
        if isinstance(value, bool) and value and any(marker in lowered_key for marker in key_markers):
            add_hint(f"{key}=true")
        elif isinstance(value, (int, float)) and value and any(marker in lowered_key for marker in key_markers):
            add_hint(f"{key}={value}")

    walk(dynamic)
    return " / ".join(hints[:4])


def is_restricted_placeholder_text(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    markers = (
        "充电专属",
        "包月专属",
        "专属动态",
        "开通后可见",
        "开通包月",
        "开通充电",
        "付费可见",
        "仅粉丝可见",
        "暂无权限",
        "无权查看",
    )
    return any(marker in text for marker in markers)


def is_page_title_text(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    return text.endswith("的动态 - 哔哩哔哩") or text.endswith("的动态-哔哩哔哩")


def fetch_dynamic_item(dynamic_ref: str, config: dict[str, Any]) -> dict[str, str]:
    dynamic_id = parse_dynamic_id(dynamic_ref)
    cookie = str(config.get("bilibili_cookie", "")).strip() or None
    users = normalize_up_users(config.get("up_users", []))
    fallback_user = users[0]

    detail = request_dynamic_detail(dynamic_id, fallback_user.uid, cookie)
    if not detail:
        html_state = request_opus_initial_state(dynamic_id, fallback_user.uid, cookie)
        html_detail = html_state.get("detail") if isinstance(html_state, dict) else None
        if not isinstance(html_detail, dict):
            raise WatcherError(f"Bilibili detail API returned no item for dynamic_id={dynamic_id}")
        detail = html_detail

    item = extract_dynamic(detail, fallback_user.uid, fallback_user.name)
    item = fill_dynamic_detail_text(item, fallback_user.uid, fallback_user.name, cookie, detail)
    return item


def extract_dynamic_text(dynamic: dict[str, Any]) -> str:
    legacy_card = dynamic.get("card") if isinstance(dynamic.get("card"), dict) else {}
    legacy_item = legacy_card.get("item") if isinstance(legacy_card.get("item"), dict) else {}
    for key in ("description", "content", "title"):
        legacy_text = str(legacy_item.get(key) or "").strip()
        if legacy_text:
            return legacy_text

    modules = dynamic.get("modules", {}) if isinstance(dynamic.get("modules"), dict) else {}
    if isinstance(dynamic.get("modules"), list):
        opus_module_text = extract_opus_modules_text(dynamic.get("modules"))
        if opus_module_text:
            return opus_module_text

    dynamic_module = modules.get("module_dynamic", dynamic)
    desc = dynamic_module.get("desc") or {}
    major = dynamic_module.get("major") or {}

    direct_text = str(desc.get("text") or "").strip()
    if direct_text:
        return direct_text

    rich_text = rich_text_nodes_to_text(desc.get("rich_text_nodes") or [])
    if rich_text:
        return rich_text

    any_rich_text = find_rich_text_nodes(dynamic)
    if any_rich_text:
        return any_rich_text

    opus = major.get("opus") or {}
    opus_summary = opus.get("summary") or {}
    opus_rich_text = rich_text_nodes_to_text(opus_summary.get("rich_text_nodes") or [])
    if opus_rich_text:
        return opus_rich_text

    opus_text = str(opus_summary.get("text") or opus.get("title") or "").strip()
    if opus_text:
        return opus_text

    return find_best_text(dynamic)


def extract_opus_modules_text(modules: Any) -> str:
    if not isinstance(modules, list):
        return ""

    parts: list[str] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        content = module.get("module_content") or {}
        paragraphs = content.get("paragraphs") or []
        if not isinstance(paragraphs, list):
            continue
        for paragraph in paragraphs:
            if not isinstance(paragraph, dict):
                continue
            text_obj = paragraph.get("text") or {}
            nodes = text_obj.get("nodes") or []
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                word = node.get("word") or {}
                words = str(word.get("words") or "").strip()
                if words:
                    parts.append(words)

    return "\n".join(parts).strip()


def find_rich_text_nodes(value: Any) -> str:
    results: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            text = rich_text_nodes_to_text(item.get("rich_text_nodes"))
            if text:
                results.append(text)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return max(results, key=len, default="")


def rich_text_nodes_to_text(nodes: Any) -> str:
    parts: list[str] = []
    if not isinstance(nodes, list):
        return ""

    for node in nodes:
        if not isinstance(node, dict):
            continue
        text = str(node.get("text") or node.get("orig_text") or "").strip()
        if text:
            parts.append(text)

    return "".join(parts).strip()


def find_best_text(value: Any) -> str:
    candidates: list[str] = []
    text_keys = {
        "comment",
        "content",
        "content_text",
        "desc",
        "description",
        "message",
        "orig_text",
        "plain_text",
        "raw_text",
        "summary",
        "text",
        "title",
    }

    def walk(item: Any, key: str = "", path: str = "") -> None:
        if isinstance(item, dict):
            for child_key, child_value in item.items():
                child_key = str(child_key)
                child_path = f"{path}.{child_key}" if path else child_key
                walk(child_value, child_key, child_path)
            return
        if isinstance(item, list):
            for child in item:
                walk(child, key, path)
            return
        if not isinstance(item, str):
            return

        text = item.strip()
        if not text or len(text) < 8:
            return
        if text.startswith(("http://", "https://")):
            return
        if is_module_summary_text(text):
            return
        if is_restricted_placeholder_text(text) or is_page_title_text(text):
            return
        if ".major." in f".{path}." and ".opus." not in f".{path}.":
            return
        if key not in text_keys:
            return
        candidates.append(text)

    walk(value)
    return max(candidates, key=len, default="")


def dump_dynamic_debug(dynamic_id: str, payload: dict[str, Any]) -> None:
    if not dynamic_id:
        return
    path = Path(f"debug_dynamic_{dynamic_id}.json")
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.warning("Dynamic %s text is still fallback; saved debug JSON to %s", dynamic_id, path)
    except OSError as exc:
        logging.warning("Failed to save dynamic debug JSON for %s: %s", dynamic_id, exc)


def is_module_summary_text(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    prefixes = (
        "发布图片动态：",
        "发布新动态：",
        "发布直播相关动态",
        "直播预约：",
        "预约：",
        "鍙戝竷鍥剧墖鍔ㄦ€侊細",
        "鍙戝竷鏂板姩鎬侊細",
        "鍙戝竷鐩存挱鐩稿叧鍔ㄦ€",
    )
    exact_summaries = {
        "发布新动态",
        "鍙戝竷鏂板姩鎬",
    }
    return text in exact_summaries or text.startswith(prefixes)


def summarize_major(major: dict[str, Any], major_type: str) -> str:
    archive = major.get("archive") or {}
    article = major.get("article") or {}
    draw = major.get("draw") or {}
    live_rcmd = major.get("live_rcmd") or {}
    common = major.get("common") or {}

    if archive:
        return f"发布视频：{archive.get('title') or archive.get('desc') or '未命名视频'}"
    if article:
        return f"发布专栏：{article.get('title') or '未命名专栏'}"
    if draw:
        count = len(draw.get("items") or [])
        return f"发布图片动态：{count} 张图片"
    if live_rcmd:
        return "发布直播相关动态"
    if common:
        return str(common.get("title") or common.get("desc") or "发布新动态")
    return f"发布新动态：{major_type}" if major_type else "发布新动态"


def format_message(item: dict[str, str], keyword: str | None = None) -> tuple[str, str]:
    is_restricted = "充电专属动态" in item.get("text", "") or "is_only_fans" in item.get("access_hint", "")
    title = f"{item['author']} 发布了{'充电专属动态' if is_restricted else '新动态'}"
    body = (
        f"{escape_markdown(item['text'][:1200])}\n\n"
        f"**UP主：** {escape_markdown(item['author'])}\n"
        f"**时间：** {escape_markdown(item['pub_time'] or '未知')}\n\n"
        f"[打开动态]({item['url']})"
    )
    if keyword:
        title = f"{keyword} {title}"
        body = f"{escape_markdown(keyword)}\n\n{body}"
    return title, body


def escape_markdown(text: str) -> str:
    # Feishu markdown is fairly permissive. Keep escaping minimal for readability.
    return text.replace("<", "&lt;").replace(">", "&gt;")


def check_once(config: dict[str, Any], state_path: Path, dry_run: bool = False) -> int:
    users = normalize_up_users(config.get("up_users", []))
    webhook = str(config.get("feishu_webhook", "")).strip()
    secret = str(config.get("feishu_secret", "")).strip() or None
    keyword = str(config.get("feishu_keyword", "")).strip() or None
    cookie = str(config.get("bilibili_cookie", "")).strip() or None
    prefer_bilibili_cookie = bool(config.get("prefer_bilibili_cookie", False))
    strip_read_state_cookies = bool(config.get("strip_bilibili_read_state_cookies", True))
    notify_on_first_run = bool(config.get("notify_on_first_run", False))

    if not webhook and not dry_run:
        raise WatcherError("Config field feishu_webhook is required unless --dry-run is used")

    state = normalize_state(load_json(state_path) if state_path.exists() else {"last_dynamic_ids": {}})
    if dry_run:
        state = copy.deepcopy(state)
    last_ids = state.setdefault("last_dynamic_ids", {})
    state.setdefault("seen_dynamic_ids", {})
    sent_count = 0

    for user in users:
        dynamics = request_dynamics(
            user.uid,
            cookie,
            prefer_cookie=prefer_bilibili_cookie,
            strip_read_state=strip_read_state_cookies,
        )
        if not dynamics:
            logging.info("No dynamic found for uid=%s", user.uid)
            continue

        feed_dynamics = sorted_feed_dynamics(dynamics)
        if not feed_dynamics:
            logging.info("No valid dynamic id found for uid=%s", user.uid)
            continue
        old_id = str(last_ids.get(user.uid, ""))
        latest_dynamic = feed_dynamics[-1]
        latest_item = extract_dynamic(latest_dynamic, user.uid, user.name)
        latest_item = fill_dynamic_detail_text(latest_item, user.uid, user.name, cookie, latest_dynamic)
        if not latest_item["id"]:
            logging.warning("Skip uid=%s because latest dynamic has no id", user.uid)
            continue

        fetched_ids: list[str] = []
        for dynamic in feed_dynamics:
            fetched_item = extract_dynamic(dynamic, user.uid, user.name)
            if fetched_item["id"]:
                fetched_ids.append(fetched_item["id"])

        is_first_seen = not old_id
        if old_id == latest_item["id"]:
            logging.info("No new dynamic for %s, latest=%s", latest_item["author"], latest_item["id"])
            remember_seen(state, user.uid, fetched_ids)
            if dry_run:
                logging.info("[dry-run] State not saved")
            continue

        if is_first_seen and not notify_on_first_run:
            last_ids[user.uid] = latest_item["id"]
            remember_seen(state, user.uid, fetched_ids)
            logging.info(
                "Initialize %s with latest dynamic %s; no notification sent",
                latest_item["author"],
                latest_item["id"],
            )
            if dry_run:
                logging.info("[dry-run] State not saved")
            continue

        if is_first_seen:
            pending = [latest_item]
        else:
            pending: list[dict[str, str]] = []
            old_id_int = int(old_id) if old_id.isdigit() else 0
            old_dynamic = next(
                (
                    dynamic
                    for dynamic in dynamics
                    if str(dynamic.get("id_str") or dynamic.get("id") or "") == old_id
                ),
                None,
            )
            if old_dynamic and is_pinned_dynamic(old_dynamic):
                logging.warning(
                    "Previous dynamic %s for uid=%s is pinned; sending only the latest non-baseline dynamic.",
                    old_id,
                    user.uid,
                )
                pending = [latest_item]
            else:
                for dynamic in feed_dynamics:
                    item = extract_dynamic(dynamic, user.uid, user.name)
                    item = fill_dynamic_detail_text(item, user.uid, user.name, cookie, dynamic)
                    if not item["id"]:
                        continue
                    item_id_int = int(item["id"]) if item["id"].isdigit() else 0
                    if item_id_int <= old_id_int:
                        continue
                    pending.append(item)
                if pending and old_id not in fetched_ids:
                    logging.warning(
                        "Previous dynamic %s for uid=%s was not found in the current feed; "
                        "sending dynamics newer than that id from the current feed.",
                        old_id,
                        user.uid,
                    )

        for item in pending:
            title, body = format_message(item, keyword)
            if dry_run:
                logging.info("[dry-run] Would send: %s\n%s", title, body)
            else:
                post_to_feishu(webhook, title, body, secret)
                logging.info("Sent dynamic %s from %s", item["id"], item["author"])
                last_ids[user.uid] = item["id"]
                remember_seen(state, user.uid, [item["id"]])
                save_json(state_path, state)
            sent_count += 1

        last_ids[user.uid] = latest_item["id"]
        remember_seen(state, user.uid, fetched_ids)

    if dry_run:
        logging.info("[dry-run] Final state not saved")
    else:
        save_json(state_path, state)
    return sent_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch Bilibili dynamics and notify Feishu.")
    parser.add_argument("--config", default="config.json", help="Path to config JSON.")
    parser.add_argument("--state", default=None, help="Path to state JSON. Default: beside config.")
    parser.add_argument("--once", action="store_true", help="Run one check and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print messages without sending Feishu.")
    parser.add_argument("--test-dynamic", default=None, help="Fetch one Bilibili dynamic URL/id and preview it.")
    parser.add_argument("--send-test-dynamic", action="store_true", help="Send --test-dynamic result to Feishu.")
    parser.add_argument("--import-cookie-har", default=None, help="Import Bilibili Cookie from a HAR opus request into config.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config_path = Path(args.config).resolve()
    if args.import_cookie_har:
        import_bilibili_cookie_from_har(config_path, Path(args.import_cookie_har).resolve())
        return 0

    config = load_json(config_path)
    interval_seconds = int(config.get("interval_seconds", 300))
    state_path = Path(args.state).resolve() if args.state else config_path.with_name("state.json")
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")

    if args.test_dynamic:
        item = fetch_dynamic_item(args.test_dynamic, config)
        title, body = format_message(item, str(config.get("feishu_keyword", "")).strip() or None)
        print(title)
        print(body)
        if item.get("_text_is_fallback") == "1":
            logging.warning(
                "Dynamic %s still uses fallback text. If this is a charge-only post, "
                "check that bilibili_cookie belongs to an account with access.",
                item.get("id"),
            )
        if args.send_test_dynamic:
            webhook = str(config.get("feishu_webhook", "")).strip()
            secret = str(config.get("feishu_secret", "")).strip() or None
            if not webhook:
                raise WatcherError("Config field feishu_webhook is required with --send-test-dynamic")
            post_to_feishu(webhook, title, body, secret)
            logging.info("Sent test dynamic %s from %s", item["id"], item["author"])
        return 0

    stop = False

    def handle_stop(signum: int, _frame: Any) -> None:
        nonlocal stop
        logging.info("Received signal %s, stopping after current check", signum)
        stop = True

    signal.signal(signal.SIGINT, handle_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_stop)

    with single_instance_lock(lock_path):
        while not stop:
            try:
                sent_count = check_once(config, state_path, dry_run=args.dry_run)
                logging.info("Check finished, sent_count=%s", sent_count)
            except WatcherError as exc:
                logging.error("Check failed: %s", exc)
            except Exception:
                logging.exception("Check failed")

            if args.once:
                break

            sleep_for = max(30, interval_seconds)
            for _ in range(sleep_for):
                if stop:
                    break
                time.sleep(1)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WatcherError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
