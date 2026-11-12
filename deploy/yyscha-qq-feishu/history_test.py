"""Preview/send up to five historical format samples without touching the live DB."""
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import time
import relay as r


def fetch_day(source, day, uid):
    page = source.page(day, uid, 0)
    total = page['total']
    scope = page['day']['chat_key']
    offset, items = 0, []
    while offset < total:
        if offset:
            page = source.page(day, uid, offset)
        if page['total'] < total or page['day']['chat_key'] != scope or not page['items']:
            raise r.Failure('��ʷ������ҳ�仯����ǰΪ�գ����Ժ�����')
        items.extend(page['items'][:total - offset])
        offset += len(page['items'])
        if offset < total and offset > 600000:
            raise r.Failure('��ʷ������ҳ����')
    return items


def category(item):
    root = r.DOM(str(item.get('html') or '')).root
    reply = root.find(lambda n: n.has('reply-content'))
    from media_feishu import extract_images
    content = root.find(lambda n: n.has('content')) or root
    media = bool(extract_images(item)) or content.find(lambda n: n.tag in ('video', 'audio')) is not None or item.get('type') not in (None, '', 'text')
    return ('���ûظ�' if reply else '��ͨ��Ϣ') + ('��ý����ʾ' if media else '������')


def select_samples(items, limit=5):
    chosen, ids = [], set()
    def add(item):
        if item['id'] not in ids and len(chosen) < limit:
            chosen.append(item)
            ids.add(item['id'])
    for kind in ['��ͨ��Ϣ������', '���ûظ�������', '��ͨ��Ϣ��ý����ʾ', '���ûظ���ý����ʾ']:
        sample = next((i for i in items if category(i) == kind), None)
        if sample:
            add(sample)
    represented = {i['uid'] for i in chosen}
    for item in items:
        if item['uid'] not in represented:
            add(item)
            represented.add(item['uid'])
    for item in items:
        add(item)
    return chosen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', default=(datetime.now(r.TZ).date() - timedelta(days=1)).isoformat())
    parser.add_argument('--config', type=Path, default=r.PRIVATE / 'config.json')
    parser.add_argument('--limit', type=int, default=5, choices=range(1, 6))
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--images-only', action='store_true', help='ֻѡ����ʵͼƬ����ʷ����')
    parser.add_argument('--replies-only', action='store_true', help='ֻѡ�������õ���ʷ����')
    args = parser.parse_args()
    try:
        day = datetime.strptime(args.day, '%Y-%m-%d').date()
    except ValueError:
        raise r.Failure('���ڸ�ʽӦΪYYYY-MM-DD') from None
    if day >= datetime.now(r.TZ).date():
        raise r.Failure('��ʷ��ʽ������ʹ���������������')
    config = r.load_config(args.config)
    r.ensure_time(config)
    if args.send and not config.get('feishu_webhook'):
        raise r.Failure('��ʷ���Է���ǰ�����÷���Webhook')
    source = r.Source(config)
    items = []
    for uid in config['uids']:
        batch = fetch_day(source, day.isoformat(), uid)
        items.extend(batch)
        print(f'��ȡ {uid}��{len(batch)}������д����ʽ���ݿ�')
    from media_feishu import extract_images, ImageClient
    if args.images_only:
        items = [item for item in items if extract_images(item)]
    if args.replies_only:
        items = [item for item in items if r.message_parts(item, day.isoformat()).get('quote')]
    samples = select_samples(items, args.limit)
    if not samples:
        raise r.Failure('������û��Ŀ���û���Ϣ�ɲ���')
    # ��ʷ���Խ�ʹ���ڴ��ϴ����棬������ʽ�������ݿ⡣
    import sqlite3
    image_db = sqlite3.connect(':memory:')
    image_client = ImageClient(config, image_db) if args.send else None
    for index, item in enumerate(samples, 1):
        kind = category(item)
        body = f'����ʷ��ʽ���� {index}/{len(samples)}��\n���ڣ�{day.isoformat()} �� ������{kind}\n\n' + r.render(item, day.isoformat())
        if args.send:
            images = extract_images(item)
            parts = r.message_parts(item, day.isoformat())
            parts['test_label'] = f'����ʷ��ʽ���� {index}/{len(samples)}�� {day.isoformat()}'
            if images or parts.get('quote'):
                image_client.send(body, images, parts=parts)
            else:
                r.feishu(config, body)
            print(f'�ѷ��� {index}/{len(samples)}��{kind}������code=0')
            time.sleep(1)
        else:
            print(body, '\n')
    print('��ʽ�������ڡ���ʷ���ߡ������Ͷ��о�δ�޸ġ�')
    image_db.close()


if __name__ == '__main__':
    try:
        main()
    except r.Failure as exc:
        print(str(exc))
        raise SystemExit(1)
    except KeyboardInterrupt:
        print('��ʷ������ֹͣ')
    except Exception:
        print('��ʷ���Է��������쳣����ֹͣ��δ���ԭʼ�쳣��ƾ�ݡ�')
        raise SystemExit(1)
