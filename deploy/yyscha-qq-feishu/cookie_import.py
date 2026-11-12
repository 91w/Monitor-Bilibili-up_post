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
    if raw.lower() in ('', 'session', '�Ự', 'null', 'undefined'):
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
            raise ValueError('��Ч�ڸ�ʽ��֧��') from None
    if not expiry.is_finite() or expiry < -1:
        raise ValueError('��Ч����ֵ��Ч')
    # �����Ժ��뵼����ʱ�䣻����С����ȡ����������ǰ�й��ڡ�
    if expiry >= Decimal('100000000000'):
        expiry /= 1000
    return expiry


def cookie_from_txt(path, now=None):
    path = Path(path)
    if path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError('Cookie�ļ��������������վ���Cookie��')
    try:
        text = path.read_text(encoding='utf-8-sig')
    except UnicodeError:
        raise ValueError('�ļ����벻֧�֣���ʹ��UTF-8��ʽ��cookies.txt��') from None
    return parse_cookie_text(text, now)


def parse_cookie_text(text, now=None):
    if len(text.encode('utf-8')) > 5 * 1024 * 1024:
        raise ValueError('Cookie�ı�����')
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
            raise ValueError('���Ǳ�׼Netscape cookies.txt��ʽ�������������cookies.txt�����������µ�����')
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
            raise ValueError(f'��{line_number}����վCookie����Ч�ڸ�ʽ�ݲ�֧�֡���֧������/С���롢���롢��ʱ�����ںͻỰ��ǣ��뻻��Netscape��ʽ��������Ҫ�ϴ�����Cookie��') from None
        if expiry > 0 and expiry <= Decimal(str(now)):
            continue
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or any(ord(c) < 32 or ord(c) > 126 or c == ';' for c in value):
            raise ValueError('��վCookie����֧�ֵ��ַ��������µ�����')
        result.append((cookie_path, name, value))
    if not result:
        if not records:
            raise ValueError('�ļ���û��Cookie��¼�����¼yyscha��վ�����µ�����')
        raise ValueError('û��������www.yyscha.cn��Ϣ�ӿڵ�δ����Cookie�����¼��վ�����µ�����')
    result.sort(key=lambda row: -len(row[0]))
    return '; '.join(f'{name}={value}' for _, name, value in result), len(result)
