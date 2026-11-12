"""Signing reused from 91w/Monitor-Bilibili-up_post; see NOTICE.md."""
import base64
import hashlib
import hmac

def feishu_signature(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")

