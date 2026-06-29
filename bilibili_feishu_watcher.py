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
import hashlib
import hmac
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


BILIBILI_DYNAMIC_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
BILIBILI_DYNAMIC_DETAIL_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail"
BILIBILI_LEGACY_DYNAMIC_DETAIL_URL = "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail"
BILIBILI_DYNAMIC_FEATURES = (
    "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote,decorationCard,"
    "forwardListHidden,ugcDelete,onlyfansQaCard"
)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


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
) -> dict[str, Any]:
    attempts = 3
    cookie_options = [cookie] if cookie else [None]
    if cookie:
        cookie_options.append(None)

    last_error: Exception | None = None
    for cookie_option in cookie_options:
        if cookie_option is None and cookie:
            logging.warning("Retrying Bilibili %s without cookie after network errors", label)

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


def request_dynamics(uid: str, cookie: str | None) -> list[dict[str, Any]]:
    payload = request_bilibili_json(
        BILIBILI_DYNAMIC_URL,
        {
            "host_mid": uid,
            "timezone_offset": "-480",
            "features": BILIBILI_DYNAMIC_FEATURES,
        },
        uid,
        cookie,
        "feed",
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


def extract_dynamic(dynamic: dict[str, Any], fallback_uid: str, fallback_name: str | None) -> dict[str, str]:
    modules = dynamic.get("modules", {})
    author = modules.get("module_author", {})
    dynamic_module = modules.get("module_dynamic", {})
    desc = dynamic_module.get("desc") or {}
    major = dynamic_module.get("major") or {}

    dynamic_id = str(dynamic.get("id_str") or dynamic.get("id") or "")
    author_name = str(author.get("name") or fallback_name or fallback_uid)
    pub_time = str(author.get("pub_time") or author.get("pub_ts") or "")

    text = extract_dynamic_text(dynamic)
    major_type = str(major.get("type") or "").strip()

    url = f"https://t.bilibili.com/{dynamic_id}" if dynamic_id else f"https://space.bilibili.com/{fallback_uid}/dynamic"

    text_is_fallback = False
    if not text:
        text_is_fallback = True
        text = summarize_major(major, major_type)

    return {
        "id": dynamic_id,
        "author": author_name,
        "pub_time": pub_time,
        "type": major_type or "dynamic",
        "text": text or "(无文字内容)",
        "_text_is_fallback": "1" if text_is_fallback else "0",
        "url": url,
    }


def fill_dynamic_detail_text(
    item: dict[str, str],
    uid: str,
    fallback_name: str | None,
    cookie: str | None,
    source_dynamic: dict[str, Any] | None = None,
) -> dict[str, str]:
    if item.get("_text_is_fallback") != "1" or not item.get("id"):
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
            detail_item.get("_text_is_fallback") != "1"
            and detail_item.get("text")
            and not is_module_summary_text(detail_item["text"])
        ):
            logging.debug("Filled dynamic %s text from detail API", item["id"])
            item["text"] = detail_item["text"]
            item["_text_is_fallback"] = "0"
            if detail_item.get("type"):
                item["type"] = detail_item["type"]
            return item
        if detail_item.get("text"):
            logging.debug("Ignored dynamic %s module summary from detail API: %s", item["id"], detail_item["text"])

    try:
        legacy_detail = request_legacy_dynamic_detail(item["id"], uid, cookie)
    except Exception as exc:
        logging.warning("Failed to fetch legacy dynamic detail %s: %s", item["id"], exc)

    if legacy_detail:
        legacy_text = extract_dynamic_text(legacy_detail)
        if legacy_text and not is_module_summary_text(legacy_text):
            logging.debug("Filled dynamic %s text from legacy detail API", item["id"])
            item["text"] = legacy_text
            item["_text_is_fallback"] = "0"
            return item

    dump_dynamic_debug(item["id"], {"feed": source_dynamic, "detail": detail, "legacy_detail": legacy_detail})
    return item


def extract_dynamic_text(dynamic: dict[str, Any]) -> str:
    legacy_card = dynamic.get("card") if isinstance(dynamic.get("card"), dict) else {}
    legacy_item = legacy_card.get("item") if isinstance(legacy_card.get("item"), dict) else {}
    for key in ("description", "content", "title"):
        legacy_text = str(legacy_item.get(key) or "").strip()
        if legacy_text:
            return legacy_text

    modules = dynamic.get("modules", {}) if isinstance(dynamic.get("modules"), dict) else {}
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
    return text.startswith(prefixes)


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
    title = f"{item['author']} 发布了新动态"
    body = (
        f"{escape_markdown(item['text'][:1200])}\n\n"
        f"**UP主：** {escape_markdown(item['author'])}\n"
        f"**类型：** {escape_markdown(item['type'])}\n"
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
    notify_on_first_run = bool(config.get("notify_on_first_run", False))

    if not webhook and not dry_run:
        raise WatcherError("Config field feishu_webhook is required unless --dry-run is used")

    state = load_json(state_path) if state_path.exists() else {"last_dynamic_ids": {}}
    last_ids = state.setdefault("last_dynamic_ids", {})
    sent_count = 0

    for user in users:
        dynamics = request_dynamics(user.uid, cookie)
        if not dynamics:
            logging.info("No dynamic found for uid=%s", user.uid)
            continue

        old_id = str(last_ids.get(user.uid, ""))
        latest_item = extract_dynamic(dynamics[0], user.uid, user.name)
        latest_item = fill_dynamic_detail_text(latest_item, user.uid, user.name, cookie, dynamics[0])
        if not latest_item["id"]:
            logging.warning("Skip uid=%s because latest dynamic has no id", user.uid)
            continue

        is_first_seen = not old_id
        if old_id == latest_item["id"]:
            logging.info("No new dynamic for %s, latest=%s", latest_item["author"], latest_item["id"])
            continue

        if is_first_seen and not notify_on_first_run:
            last_ids[user.uid] = latest_item["id"]
            logging.info(
                "Initialize %s with latest dynamic %s; no notification sent",
                latest_item["author"],
                latest_item["id"],
            )
            continue

        if is_first_seen:
            pending = [latest_item]
        else:
            pending: list[dict[str, str]] = []
            for dynamic in dynamics:
                item = extract_dynamic(dynamic, user.uid, user.name)
                item = fill_dynamic_detail_text(item, user.uid, user.name, cookie, dynamic)
                if not item["id"]:
                    continue
                if item["id"] == old_id:
                    break
                pending.append(item)

        for item in reversed(pending):
            title, body = format_message(item, keyword)
            if dry_run:
                logging.info("[dry-run] Would send: %s\n%s", title, body)
            else:
                post_to_feishu(webhook, title, body, secret)
                logging.info("Sent dynamic %s from %s", item["id"], item["author"])
                last_ids[user.uid] = item["id"]
                save_json(state_path, state)
            sent_count += 1

        last_ids[user.uid] = latest_item["id"]

    save_json(state_path, state)
    return sent_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch Bilibili dynamics and notify Feishu.")
    parser.add_argument("--config", default="config.json", help="Path to config JSON.")
    parser.add_argument("--state", default=None, help="Path to state JSON. Default: beside config.")
    parser.add_argument("--once", action="store_true", help="Run one check and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print messages without sending Feishu.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config_path = Path(args.config).resolve()
    config = load_json(config_path)
    interval_seconds = int(config.get("interval_seconds", 300))
    state_path = Path(args.state).resolve() if args.state else config_path.with_name("state.json")

    stop = False

    def handle_stop(signum: int, _frame: Any) -> None:
        nonlocal stop
        logging.info("Received signal %s, stopping after current check", signum)
        stop = True

    signal.signal(signal.SIGINT, handle_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_stop)

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
