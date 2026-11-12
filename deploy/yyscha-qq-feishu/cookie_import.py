"""Read a local Netscape cookies.txt without copying it or exposing values."""
from pathlib import Path
from decimal import Decimal, InvalidOperation
from datetime import datetime
import re
import time

HOST = 'www.yyscha.cn'
API_PATH = '/application/QQ/api.php'


def parse_expiry(raw):
    raw = raw.strip()
    if raw.lower() in ('', 'session', '会话', 'null', 'undefined'):
        return Decimal(0)
    try:
        expiry = Decimal(raw)
    except InvalidOperation:
        try:
            date = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if date.tzinfo is None:
                raise ValueError
            return Decimal(str(date.timestamp()))
        except (ValueError, OverflowError, OSError):
            raise ValueError('有效期格式不支持') from None
    if not expiry.is_finite() or expiry < -1:
        raise ValueError('有效期数值无效')
    # 兼容以毫秒导出的时间；不对小数秒取整，以免提前判过期。
    if expiry >= Decimal('100000000000'):
        expiry /= 1000
    return expiry


def cookie_from_txt(path, now=None):
    path = Path(path)
    if path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError('Cookie文件过大，请仅导出网站相关Cookie。')
    try:
        text = path.read_text(encoding='utf-8-sig')
    except UnicodeError:
        raise ValueError('文件编码不支持，请使用UTF-8格式的cookies.txt。') from None
    return parse_cookie_text(text, now)


def parse_cookie_text(text, now=None):
    if len(text.encode('utf-8')) > 5 * 1024 * 1024:
        raise ValueError('Cookie文本过大。')
    now = time.time() if now is None else now
    result = []
    records = 0
    for line_number, line in enumerate(text.splitlines(), 1):
        if line.startswith('#HttpOnly_'):
            line = line[len('#HttpOnly_'):]
        if not line.strip() or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) != 7:
            raise ValueError('不是标准Netscape cookies.txt格式，请用浏览器的cookies.txt导出功能重新导出。')
        domain, subdomains, cookie_path, secure, expires, name, value = parts
        records += 1
        domain = domain.lower().lstrip('.')
        if domain != HOST and not (domain == 'yyscha.cn' and subdomains.upper() == 'TRUE'):
            continue
        if not cookie_path.startswith('/') or not (API_PATH == cookie_path or (API_PATH.startswith(cookie_path) and (cookie_path.endswith('/') or API_PATH[len(cookie_path):].startswith('/')))):
            continue
        try:
            expiry = parse_expiry(expires)
        except ValueError:
            raise ValueError(f'第{line_number}行网站Cookie的有效期格式暂不支持。已支持整数/小数秒、毫秒、带时区日期和会话标记；请换用Netscape格式导出，不要上传完整Cookie。') from None
        if expiry > 0 and expiry <= Decimal(str(now)):
            continue
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or any(ord(c) < 32 or ord(c) > 126 or c == ';' for c in value):
            raise ValueError('网站Cookie含不支持的字符，请重新导出。')
        result.append((cookie_path, name, value))
    if not result:
        if not records:
            raise ValueError('文件中没有Cookie记录，请登录yyscha网站后重新导出。')
        raise ValueError('没有适用于www.yyscha.cn消息接口的未过期Cookie，请登录网站后重新导出。')
    result.sort(key=lambda row: -len(row[0]))
    return '; '.join(f'{name}={value}' for _, name, value in result), len(result)
