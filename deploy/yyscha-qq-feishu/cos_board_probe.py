"""One synthetic desktop->COS->board->COS->desktop test, using board-only keys."""
import argparse
import json
from pathlib import Path
import uuid

from cos_transport import CosClient, CloudError, load_credentials
from device_pairing import load_pair
import secure_package as p


def prepare(private, manifest):
    identity, peer = load_pair(private, 'desktop')
    run_id = uuid.uuid4().hex
    packet = p.seal('session=synthetic-home-board-check', 'h616-home-01', 1,
                    p.unb64(peer['decrypt_public']), p.unb64(identity['sender_secret']), package_id=run_id)
    # Only public header/ciphertext, never an identity secret or website cookie.
    manifest.write_bytes(p.canonical({'kind': 'synthetic-board-probe', 'packet': p.b64(packet)}))
    client = CosClient(load_credentials(private / 'cos-uploader.json'))
    client.request('PUT', client.object_key('inbox', run_id), data=packet)
    return {'run_id': run_id, 'uploaded': True}


def board(private, manifest):
    identity, peer = load_pair(private, 'board')
    fixture = p.parse(manifest.read_bytes())
    if fixture.get('kind') != 'synthetic-board-probe':
        raise CloudError('只允许合成板端测试')
    packet = p.unb64(fixture['packet'])
    header = p.authenticate(packet, 'h616-home-01', p.unb64(peer['sender_public']))['header']
    client = CosClient(load_credentials(private / 'cos-receiver.json'))
    key = client.object_key('inbox', header['package_id'])
    # Establish that this came from cloud storage, rather than merely a USB fixture.
    downloaded = client.request('GET', key)
    if downloaded != packet:
        raise CloudError('云端合成测试包与预期不符')
    _, cookie = p.open_package(downloaded, 'h616-home-01', p.unb64(identity['decrypt_public']),
                               p.unb64(identity['decrypt_secret']), p.unb64(peer['sender_public']))
    if cookie != 'session=synthetic-home-board-check':
        raise CloudError('只允许合成会话，未应用网站Cookie')
    signed = p.receipt(header, packet, 'applied', p.unb64(identity['receipt_secret']))
    client.request('PUT', client.object_key('receipts', header['package_id']), data=signed)
    client.request('DELETE', key)
    return {'run_id': header['package_id'], 'board_downloaded': True,
            'decrypted': True, 'signed_receipt_uploaded': True, 'synthetic_inbox_deleted': True,
            'website_session_applied': False}


def verify(private, manifest):
    _, peer = load_pair(private, 'desktop')
    packet = p.unb64(p.parse(manifest.read_bytes())['packet'])
    header = p.parse(packet)['header']
    client = CosClient(load_credentials(private / 'cos-uploader.json'))
    signed = client.request('GET', client.object_key('receipts', header['package_id']))
    status = p.verify_receipt(signed, header, packet, p.unb64(peer['receipt_public']))
    if status != 'applied':
        raise CloudError('合成回执状态不符')
    return {'run_id': header['package_id'], 'board_receipt_authenticated': True, 'synthetic_only': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'board', 'verify'))
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(globals()[args.action](args.private, args.manifest)))
    except Exception:
        print('板端合成传输测试未完成；未输出凭据、私钥或原始异常。')
        raise SystemExit(1)
