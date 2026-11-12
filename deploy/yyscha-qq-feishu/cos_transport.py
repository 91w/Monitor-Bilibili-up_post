"""Bounded COS XML REST transport. Authentication follows Tencent COS v5.

Reference: https://cloud.tencent.com/document/product/436/7778
No cloud credentials, signed URLs, or server bodies belong in diagnostics.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, unquote
from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler, HTTPRedirectHandler
import xml.etree.ElementTree as ET

BUCKET = os.environ.get('YYSCHA_COS_BUCKET', 'example-relay-1234567890')
REGION = os.environ.get('YYSCHA_COS_REGION', 'ap-beijing')
DEVICE = 'h616-home-01'
MAX_OBJECT = 256 * 1024
MAX_LIST = 512 * 1024
PRIVATE = Path(os.environ.get('YYSCHA_PRIVATE_DIR', str(Path.home() / '.yyscha-qq-feishu') if os.name == 'nt' else '/overlay/yyscha/data'))


class CloudError(Exception):
    def __init__(self, reason, status=None, code=None):
        self.reason, self.status, self.code = reason, status, code
        super().__init__(reason + (f' (HTTP {status})' if status is not None else '') +
                         (f' [{code}]' if code else ''))


def check_time():
    if time.time() < 1767225600:
        raise CloudError('系统时间明显不可信，请先校时')
    if os.name != 'nt':
        from timeguard import require_trusted_time, TimeTrustError
        try:
            require_trusted_time()
        except TimeTrustError:
            raise CloudError('开发板时间未通过NTP可信检查，暂不签名请求') from None


def encode_fields(values):
    encoded = {quote(str(k), safe='').lower(): quote(str(v), safe='') for k, v in values.items()}
    keys = sorted(encoded)
    return ';'.join(keys), '&'.join(k + '=' + encoded[k] for k in keys)


def http_string(method, path, params, headers):
    return '\n'.join((method.lower(), path, encode_fields(params)[1], encode_fields(headers)[1], ''))


def authorization(method, path, params, headers, secret_id, secret_key, now=None):
    now = int(time.time()) if now is None else int(now)
    key_time = f'{now - 30};{now + 300}'
    sign_key = hmac.new(secret_key.encode(), key_time.encode(), hashlib.sha1).hexdigest()
    digest = hashlib.sha1(http_string(method, path, params, headers).encode()).hexdigest()
    signed = f'sha1\n{key_time}\n{digest}\n'
    signature = hmac.new(sign_key.encode(), signed.encode(), hashlib.sha1).hexdigest()
    return ('q-sign-algorithm=sha1&q-ak=' + secret_id + '&q-sign-time=' + key_time +
            '&q-key-time=' + key_time + '&q-header-list=' + encode_fields(headers)[0] +
            '&q-url-param-list=' + encode_fields(params)[0] + '&q-signature=' + signature)


def validate_credentials(value):
    if not isinstance(value, dict) or any(value.get(k) != v for k, v in
                                         [('bucket', BUCKET), ('region', REGION), ('device', DEVICE)]):
        raise CloudError('中转配置与本项目固定桶或设备不符')
    for field in ('secret_id', 'secret_key'):
        text = value.get(field)
        if not isinstance(text, str) or not re.fullmatch(r'[A-Za-z0-9_+/=.-]{16,128}', text):
            raise CloudError('云访问凭据为空或格式无效，请在本机重新填写')
    return value


def load_credentials(path):
    try:
        return validate_credentials(json.loads(Path(path).read_text(encoding='utf-8-sig')))
    except CloudError:
        raise
    except Exception:
        raise CloudError('无法读取本机私密云配置，请先填写') from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise CloudError('中转服务重定向已拒绝，请核对地域和桶名')


class CosClient:
    def __init__(self, credentials=None, opener=None):
        self.credentials = validate_credentials(credentials) if credentials is not None else None
        self.host = BUCKET + '.cos.' + REGION + '.myqcloud.com'
        self.prefix = f'devices/{DEVICE}/'
        ca = Path('/overlay/b-up-runtime/python/lib/python3.11/site-packages/certifi/cacert.pem')
        context = ssl.create_default_context(cafile=str(ca) if ca.is_file() else None)
        self.opener = opener or build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect)

    def request(self, method, key='', params=None, data=None, authenticated=True, max_bytes=MAX_OBJECT):
        # This low-level entry is also used for explicit negative permission probes.
        # Endpoint is fixed; keys cannot contain URLs, query syntax or traversal.
        if method not in ('GET', 'PUT', 'DELETE') or not re.fullmatch(r'[A-Za-z0-9_./-]*', key):
            raise CloudError('不支持的中转请求')
        if key.startswith('/') or any(p in ('.', '..') for p in key.split('/')):
            raise CloudError('中转对象路径无效')
        params = dict(params or {})
        if any(k not in ('prefix', 'marker', 'max-keys', 'encoding-type', 'acl') for k in params):
            raise CloudError('不支持的中转查询参数')
        if data is not None and (not isinstance(data, bytes) or len(data) > MAX_OBJECT):
            raise CloudError('中转上传内容超限')
        path = '/' + key
        headers = {'host': self.host}
        if data is not None:
            headers.update({'content-type': 'application/json', 'content-length': str(len(data)),
                            'content-md5': base64.b64encode(hashlib.md5(data).digest()).decode()})
        if authenticated:
            check_time()
            if self.credentials is None:
                raise CloudError('尚未填写中转访问凭据')
            headers['Authorization'] = authorization(method, path, params, headers,
                self.credentials['secret_id'], self.credentials['secret_key'])
        query = urlencode(params, quote_via=quote, safe='')
        url = 'https://' + self.host + path + ('?' + query if query else '')
        try:
            with self.opener.open(Request(url, data=data, headers=headers, method=method), timeout=20) as response:
                status = response.status
                if status not in (200, 201, 204):
                    raise CloudError('中转返回非成功状态', status)
                size = response.headers.get('Content-Length')
                if size is not None and int(size) > max_bytes:
                    raise CloudError('中转下载内容超限')
                content = response.read(max_bytes + 1)
                if len(content) > max_bytes:
                    raise CloudError('中转下载内容超限')
                return content
        except HTTPError as exc:
            status = exc.code
            code = None
            try:
                error = exc.read(8193)
                if len(error) <= 8192 and b'<!' not in error:
                    candidate = ET.fromstring(error).findtext('Code')
                    if candidate in ('AccessDenied', 'SignatureDoesNotMatch', 'InvalidAccessKeyId',
                                     'RequestTimeTooSkewed', 'NoSuchKey', 'NoSuchBucket',
                                     'ExpiredToken', 'InvalidToken'):
                        code = candidate
            except Exception:
                pass
            finally:
                exc.close()
            raise CloudError('中转请求未通过', status, code) from None
        except CloudError:
            raise
        except (URLError, TimeoutError, OSError, ValueError):
            raise CloudError('中转网络、证书或响应异常，请稍后重试') from None

    def object_key(self, area, package_id):
        if area not in ('inbox', 'receipts') or not re.fullmatch(r'[0-9a-f]{32}', package_id):
            raise CloudError('中转对象标识无效')
        return self.prefix + area + '/' + package_id + '.json'

    def list_inbox(self, marker=''):
        prefix = self.prefix + 'inbox/'
        params = {'prefix': prefix, 'max-keys': '100', 'encoding-type': 'url'}
        if marker:
            if not marker.startswith(prefix) or len(marker) > 256:
                raise CloudError('中转分页游标无效')
            params['marker'] = marker
        content = self.request('GET', params=params, max_bytes=MAX_LIST)
        try:
            if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
                raise ValueError()
            root = ET.fromstring(content)
            for node in root.iter():
                node.tag = node.tag.split('}')[-1]
            if root.tag != 'ListBucketResult' or root.findtext('Name') != BUCKET or root.findtext('EncodingType') != 'url':
                raise ValueError()
            if unquote(root.findtext('Prefix', '')) != prefix:
                raise ValueError()
            entries = root.findall('Contents')
            if len(entries) > 100:
                raise ValueError()
            result = []
            for node in entries:
                key = unquote(node.findtext('Key', ''))
                size = int(node.findtext('Size', '-1'))
                if not key.startswith(prefix) or not 0 <= size <= MAX_OBJECT:
                    raise ValueError()
                leaf = key[len(prefix):]
                # Foreign objects under the prefix cannot become package inputs.
                if re.fullmatch(r'[0-9a-f]{32}\.json', leaf):
                    result.append({'key': key, 'size': size})
            truncated = root.findtext('IsTruncated')
            if truncated not in ('true', 'false'):
                raise ValueError()
            next_marker = unquote(root.findtext('NextMarker', '')) if truncated == 'true' else ''
            if truncated == 'true' and (not next_marker.startswith(prefix) or next_marker <= marker or len(next_marker) > 256):
                raise ValueError()
            return result, next_marker
        except Exception:
            raise CloudError('中转对象列表结构、路径或大小无效') from None
