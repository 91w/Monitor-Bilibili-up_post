"""Version 1 sealed-box packages authenticated with pinned Ed25519 identities.

Cryptography is delegated entirely to libnacl/libsodium. No private key is
carried in an envelope, public receipt, diagnostic, or cloud object name.
"""
import base64
import hashlib
import json
import re
import time
import uuid

from crypto_runtime import libnacl

MAX_BYTES = 256 * 1024
MAX_TTL = 24 * 3600
DOMAIN = b'yyscha-cookie-update-v1\0'
RECEIPT_DOMAIN = b'yyscha-cookie-receipt-v1\0'
HEADER_KEYS = {'protocol', 'device', 'package_id', 'version', 'created', 'expires'}


class PackageError(Exception):
    """Credential-free protocol rejection."""


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('ascii')


def b64(data):
    return base64.b64encode(data).decode('ascii')


def unb64(value):
    if not isinstance(value, str):
        raise PackageError('invalid_encoding')
    try:
        return base64.b64decode(value, validate=True)
    except Exception:
        raise PackageError('invalid_encoding') from None


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PackageError('duplicate_field')
        result[key] = value
    return result


def parse(data):
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise PackageError('invalid_size')
    try:
        value = json.loads(data, object_pairs_hook=no_duplicates)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except PackageError:
        raise
    except Exception:
        raise PackageError('invalid_json') from None


def header_valid(header, device):
    if not isinstance(header, dict) or set(header) != HEADER_KEYS:
        raise PackageError('invalid_header')
    if type(header['protocol']) is not int or header['protocol'] != 1:
        raise PackageError('unsupported_protocol')
    if not isinstance(device, str) or not re.fullmatch(r'[a-z0-9-]{1,64}', device):
        raise PackageError('invalid_device')
    if header['device'] != device:
        raise PackageError('wrong_device')
    if not isinstance(header['package_id'], str) or not re.fullmatch(r'[0-9a-f]{32}', header['package_id']):
        raise PackageError('invalid_package_id')
    if type(header['version']) is not int or not 0 < header['version'] < 2**63:
        raise PackageError('invalid_version')
    if any(type(header[x]) is not int for x in ('created', 'expires')):
        raise PackageError('invalid_time')
    if not 0 < header['expires'] - header['created'] <= MAX_TTL:
        raise PackageError('invalid_lifetime')


def seal(cookie, device, version, recipient_public, sender_secret, now=None, package_id=None):
    now = int(time.time()) if now is None else now
    header = dict(protocol=1, device=device, version=version,
                  package_id=package_id or uuid.uuid4().hex, created=now, expires=now + MAX_TTL)
    header_valid(header, device)
    if not isinstance(cookie, str) or not cookie or len(cookie.encode()) > 32 * 1024 or any(c in cookie for c in '\r\n') or '=' not in cookie:
        raise PackageError('invalid_cookie')
    plaintext = canonical({'header': header, 'cookie': cookie})
    try:
        ciphertext = libnacl.crypto_box_seal(plaintext, recipient_public)
        protected = {'header': header, 'ciphertext': b64(ciphertext)}
        signature = libnacl.crypto_sign(DOMAIN + canonical(protected), sender_secret)[:64]
        return canonical({**protected, 'signature': b64(signature)})
    except Exception:
        raise PackageError('encryption_failed') from None


def authenticate(data, device, sender_public):
    envelope = parse(data)
    if set(envelope) != {'header', 'ciphertext', 'signature'}:
        raise PackageError('invalid_envelope')
    header_valid(envelope['header'], device)
    signed = DOMAIN + canonical({k: envelope[k] for k in ('header', 'ciphertext')})
    signature = unb64(envelope['signature'])
    if len(signature) != 64:
        raise PackageError('invalid_signature')
    try:
        libnacl.crypto_sign_open(signature + signed, sender_public)
    except Exception:
        raise PackageError('untrusted_sender_or_tampered') from None
    return envelope


def open_package(data, device, recipient_public, recipient_secret, sender_public, applied_version=0, now=None):
    envelope = authenticate(data, device, sender_public)
    header = envelope['header']
    now = int(time.time()) if now is None else now
    if header['created'] > now + 300 or header['expires'] <= now:
        raise PackageError('expired_or_untrusted_time')
    if header['version'] <= applied_version:
        raise PackageError('old_version')
    try:
        plaintext = libnacl.crypto_box_seal_open(unb64(envelope['ciphertext']), recipient_public, recipient_secret)
        inner = parse(plaintext)
    except Exception:
        raise PackageError('decryption_failed') from None
    if set(inner) != {'header', 'cookie'} or inner['header'] != header:
        raise PackageError('header_mismatch')
    cookie = inner['cookie']
    if not isinstance(cookie, str) or not cookie or len(cookie.encode()) > 32 * 1024 or any(c in cookie for c in '\r\n') or '=' not in cookie:
        raise PackageError('invalid_cookie')
    return header, cookie


def receipt(header, package_bytes, status, signing_secret):
    if status not in ('applied', 'rejected'):
        raise PackageError('invalid_status')
    body = {'protocol': 1, 'device': header['device'], 'package_id': header['package_id'],
            'version': header['version'], 'package_sha256': hashlib.sha256(package_bytes).hexdigest(),
            'status': status}
    signed = libnacl.crypto_sign(RECEIPT_DOMAIN + canonical(body), signing_secret)
    return canonical({'body': body, 'signature': b64(signed[:64])})


def verify_receipt(data, header, package_bytes, signing_public):
    value = parse(data)
    if set(value) != {'body', 'signature'} or not isinstance(value['body'], dict):
        raise PackageError('invalid_receipt')
    try:
        libnacl.crypto_sign_open(unb64(value['signature']) + RECEIPT_DOMAIN + canonical(value['body']), signing_public)
    except Exception:
        raise PackageError('invalid_receipt_signature') from None
    expected = {'protocol': 1, 'device': header['device'], 'package_id': header['package_id'],
                'version': header['version'], 'package_sha256': hashlib.sha256(package_bytes).hexdigest(),
                'status': value['body'].get('status')}
    if value['body'] != expected or expected['status'] not in ('applied', 'rejected'):
        raise PackageError('receipt_mismatch')
    return expected['status']
