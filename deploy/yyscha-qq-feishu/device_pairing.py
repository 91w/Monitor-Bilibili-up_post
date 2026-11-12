"""First pairing over a trusted channel. Never overwrites a pinned identity."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from cos_transport import DEVICE, CloudError, PRIVATE
import secure_package as p


def write_private(path, value):
    path = Path(path)
    if 'outputs' in {part.lower() for part in path.resolve().parts}:
        raise CloudError('配对数据必须位于私密目录')
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        path.parent.chmod(0o700)
    data = p.canonical(value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def public_identity(identity):
    base = {'protocol': 1, 'device': DEVICE, 'role': identity['role']}
    fields = ('decrypt_public', 'receipt_public') if identity['role'] == 'board' else ('sender_public',)
    return {**base, **{key: identity[key] for key in fields}}


def validate_public(value, expected_role):
    fields = ('decrypt_public', 'receipt_public') if expected_role == 'board' else ('sender_public',)
    if not isinstance(value, dict) or set(value) != {'protocol', 'device', 'role', *fields}:
        raise CloudError('配对公钥格式不符')
    if type(value['protocol']) is not int or value['protocol'] != 1 or value['device'] != DEVICE or value['role'] != expected_role:
        raise CloudError('配对设备或身份类型不符')
    try:
        if any(len(p.unb64(value[key])) != 32 for key in fields):
            raise ValueError()
    except Exception:
        raise CloudError('配对公钥无效') from None
    return value


def initialize(private, role):
    private = Path(private)
    path = private / 'relay-identity.json'
    if path.exists():
        identity = p.parse(path.read_bytes())
        validate_public(public_identity(identity), role)
        return identity
    if role not in ('desktop', 'board'):
        raise CloudError('配对角色无效')
    identity = {'protocol': 1, 'device': DEVICE, 'role': role}
    if role == 'board':
        pk, sk = p.libnacl.crypto_box_keypair()
        vk, signing = p.libnacl.crypto_sign_keypair()
        identity.update(decrypt_public=p.b64(pk), decrypt_secret=p.b64(sk),
                        receipt_public=p.b64(vk), receipt_secret=p.b64(signing))
    else:
        vk, signing = p.libnacl.crypto_sign_keypair()
        identity.update(sender_public=p.b64(vk), sender_secret=p.b64(signing))
    write_private(path, identity)
    return identity


def pin_peer(private, role, peer):
    expected = 'desktop' if role == 'board' else 'board'
    validate_public(peer, expected)
    identity = initialize(private, role)
    path = Path(private) / 'relay-peer.json'
    if path.exists():
        if p.parse(path.read_bytes()) != peer:
            raise CloudError('已固定不同配对身份，必须通过单独重新配对流程处理')
    else:
        write_private(path, peer)
    return hashlib.sha256(p.canonical(peer)).hexdigest()


def load_pair(private, role):
    root = Path(private)
    try:
        identity = p.parse((root / 'relay-identity.json').read_bytes())
        peer = p.parse((root / 'relay-peer.json').read_bytes())
        validate_public(public_identity(identity), role)
        validate_public(peer, 'desktop' if role == 'board' else 'board')
        return identity, peer
    except Exception:
        raise CloudError('设备身份或配对公钥不可用，请检查私密配对文件') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('init', 'pin', 'status'))
    parser.add_argument('--role', choices=('desktop', 'board'), required=True)
    parser.add_argument('--private', type=Path, default=PRIVATE)
    parser.add_argument('--public-output', type=Path)
    parser.add_argument('--peer', type=Path)
    args = parser.parse_args()
    if args.action == 'init':
        public = public_identity(initialize(args.private, args.role))
        if args.public_output:
            if args.public_output.exists():
                if p.parse(args.public_output.read_bytes()) != public:
                    raise CloudError('公钥导出目标已有不同内容，未覆盖')
            else:
                write_private(args.public_output, public)
        print(json.dumps({'role': args.role, 'fingerprint': hashlib.sha256(p.canonical(public)).hexdigest()}))
    elif args.action == 'pin':
        if not args.peer:
            parser.error('pin requires --peer')
        fingerprint = pin_peer(args.private, args.role, p.parse(args.peer.read_bytes()))
        print(json.dumps({'pinned': True, 'peer_fingerprint': fingerprint}))
    else:
        identity, peer = load_pair(args.private, args.role)
        print(json.dumps({'role': args.role, 'paired': True,
                          'own_fingerprint': hashlib.sha256(p.canonical(public_identity(identity))).hexdigest(),
                          'peer_fingerprint': hashlib.sha256(p.canonical(peer)).hexdigest()}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('配对操作未完成；已保留原身份，未输出私钥或原始异常。')
        raise SystemExit(1)
