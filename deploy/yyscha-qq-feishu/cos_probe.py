"""Synthetic COS permission checks; never reads a website cookie or sends Feishu."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from cos_transport import CosClient, CloudError, PRIVATE, load_credentials
import secure_package as p


def probe(uploader, receiver, report_path):
    run_id = uuid.uuid4().hex
    report = {'run_id': run_id, 'created_utc': datetime.now(timezone.utc).isoformat(),
              'kind': 'synthetic-only', 'checks': [], 'leftovers': []}
    inbox = uploader.object_key('inbox', run_id)
    receipt_key = uploader.object_key('receipts', run_id)
    public, private = p.libnacl.crypto_box_keypair()
    sender_public, sender_private = p.libnacl.crypto_sign_keypair()
    board_public, board_private = p.libnacl.crypto_sign_keypair()
    packet = p.seal('session=synthetic-permission-check', 'h616-home-01', 1,
                    public, sender_private, package_id=run_id)
    header = p.parse(packet)['header']
    created = False

    def check(name, action, denied=False):
        try:
            result = action()
        except CloudError as exc:
            passed = denied and exc.status == 403 and exc.code == 'AccessDenied'
            report['checks'].append({'name': name, 'passed': passed,
                                     'http': exc.status, 'code': exc.code, 'reason': exc.reason})
            return passed, None
        except Exception:
            report['checks'].append({'name': name, 'passed': False,
                                     'reason': '本地校验失败，未输出原始异常'})
            return False, None
        report['checks'].append({'name': name, 'passed': not denied,
                                 'reason': '操作成功' if not denied else '越权操作意外成功'})
        return not denied, result

    def exact_read(client, key, expected):
        value = client.request('GET', key)
        if value != expected:
            raise CloudError('合成对象内容不符')
        return value

    try:
        # An upload timeout can still have stored the object. Always attempt
        # cleanup of this freshly generated, synthetic-only key in finally.
        created = True
        ok, _ = check('上传端写本设备密文', lambda: uploader.request('PUT', inbox, data=packet))
        if not ok:
            return report
        created = True
        ok, received = check('领取端读取同一密文', lambda: exact_read(receiver, inbox, packet))
        if not ok:
            return report
        def decrypt():
            _, cookie = p.open_package(received, 'h616-home-01', public, private, sender_public)
            if cookie != 'session=synthetic-permission-check':
                raise CloudError('合成加密包校验失败')
        check('解密及发送者认证', decrypt)
        def list_and_find():
            marker = ''
            for _ in range(10):
                entries, next_marker = receiver.list_inbox(marker)
                if any(item['key'] == inbox for item in entries):
                    return
                if not next_marker:
                    break
                marker = next_marker
            raise CloudError('限定前缀列表未找到合成包')
        check('领取端仅列本设备inbox', list_and_find)
        signed_receipt = p.receipt(header, packet, 'applied', board_private)
        ok, _ = check('领取端写合成回执', lambda: receiver.request('PUT', receipt_key, data=signed_receipt))
        if ok:
            report['leftovers'].append({'key': receipt_key, 'kind': 'synthetic-receipt', 'cleanup': '3天生命周期规则'})
            def read_receipt():
                content = exact_read(uploader, receipt_key, signed_receipt)
                if p.verify_receipt(content, header, packet, board_public) != 'applied':
                    raise CloudError('合成回执认证失败')
            check('上传端读取并认证合成回执', read_receipt)
        # AccessDenied is required; 404, timeout and signature failures never pass.
        check('匿名不能读取密文', lambda: receiver.request('GET', inbox, authenticated=False), True)
        check('上传端不能读取inbox', lambda: uploader.request('GET', inbox), True)
        check('上传端不能列桶根目录', lambda: uploader.request('GET', params={'max-keys': '1'}), True)
        check('上传端不能写回执', lambda: uploader.request('PUT', receipt_key, data=signed_receipt), True)
        check('领取端不能写inbox', lambda: receiver.request('PUT', inbox, data=packet), True)
        check('领取端不能列桶根目录', lambda: receiver.request('GET', params={'max-keys': '1'}), True)
        other = 'devices/h616-probe-other/inbox/'
        check('领取端不能列其他设备', lambda: receiver.request('GET', params={'prefix': other, 'max-keys': '1'}), True)
        check('领取端不能读取其他设备', lambda: receiver.request('GET', other + run_id + '.json'), True)
        check('领取端不能读取桶ACL', lambda: receiver.request('GET', params={'acl': ''}), True)
        check('上传端不能删除inbox', lambda: uploader.request('DELETE', inbox), True)
        other_key = other + run_id + '.json'
        ok, _ = check('上传端不能写其他设备', lambda: uploader.request('PUT', other_key, data=packet), True)
        if not ok and report['checks'][-1].get('reason') == '越权操作意外成功':
            try:
                receiver.request('DELETE', other_key)
            except CloudError:
                report['leftovers'].append({'key': other_key, 'kind': 'synthetic-ciphertext', 'cleanup': '需桶管理员删除'})
    finally:
        if created:
            ok, _ = check('领取端清理合成密文', lambda: receiver.request('DELETE', inbox))
            if not ok:
                report['leftovers'].append({'key': inbox, 'kind': 'synthetic-ciphertext', 'cleanup': '3天生命周期规则'})
        report['passed'] = len(report['checks']) >= 18 and all(c['passed'] for c in report['checks'])
        report['scope'] = '电脑端使用两组身份的COS权限与合成密文测试；不是正式板端应用或网站登录验收'
        report['not_tested'] = ['HTTP请求拒绝', '最终生命周期及费用配置API核验', '板端COS联调', '正式配对及真实Cookie更新']
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        temp = report_path.with_suffix('.new')
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, report_path)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--anonymous', action='store_true')
    parser.add_argument('--report', type=Path, default=PRIVATE / 'cos-probe-result.json')
    args = parser.parse_args()
    if args.anonymous:
        try:
            CosClient().request('GET', f'devices/h616-home-01/inbox/{uuid.uuid4().hex}.json', authenticated=False)
        except CloudError as exc:
            print(json.dumps({'http': exc.status, 'code': exc.code,
                              'anonymous_denied': exc.status == 403 and exc.code == 'AccessDenied'}))
            return
        raise CloudError('匿名请求意外成功，请检查私有读写设置')
    uploader = CosClient(load_credentials(PRIVATE / 'cos-uploader.json'))
    receiver = CosClient(load_credentials(PRIVATE / 'cos-receiver-bootstrap.json'))
    if uploader.credentials['secret_id'] == receiver.credentials['secret_id']:
        raise CloudError('两端填写了相同访问身份，请分别填写各自密钥')
    report = probe(uploader, receiver, args.report)
    for check in report['checks']:
        print(('PASS ' if check['passed'] else 'FAIL ') + check['name'])
    print('权限验收通过' if report['passed'] else '权限验收未通过，请保留原策略等待排查')
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except CloudError as exc:
        print(str(exc))
        raise SystemExit(1)
    except Exception:
        print('中转测试本地异常；未输出凭据、签名或原始服务端响应。')
        raise SystemExit(1)
