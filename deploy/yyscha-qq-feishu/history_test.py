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
            raise r.Failure('历史样本分页变化或提前为空，请稍后重试')
        items.extend(page['items'][:total - offset])
        offset += len(page['items'])
        if offset < total and offset > 600000:
            raise r.Failure('历史样本分页过多')
    return items


def category(item):
    root = r.DOM(str(item.get('html') or '')).root
    reply = root.find(lambda n: n.has('reply-content'))
    from media_feishu import extract_images
    content = root.find(lambda n: n.has('content')) or root
    media = bool(extract_images(item)) or content.find(lambda n: n.tag in ('video', 'audio')) is not None or item.get('type') not in (None, '', 'text')
    return ('引用回复' if reply else '普通消息') + ('＋媒体提示' if media else '＋文字')


def select_samples(items, limit=5):
    chosen, ids = [], set()
    def add(item):
        if item['id'] not in ids and len(chosen) < limit:
            chosen.append(item)
            ids.add(item['id'])
    for kind in ['普通消息＋文字', '引用回复＋文字', '普通消息＋媒体提示', '引用回复＋媒体提示']:
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
    parser.add_argument('--images-only', action='store_true', help='只选择含真实图片的历史样本')
    parser.add_argument('--replies-only', action='store_true', help='只选择有引用的历史样本')
    args = parser.parse_args()
    try:
        day = datetime.strptime(args.day, '%Y-%m-%d').date()
    except ValueError:
        raise r.Failure('日期格式应为YYYY-MM-DD') from None
    if day >= datetime.now(r.TZ).date():
        raise r.Failure('历史格式测试请使用昨天或更早的日期')
    config = r.load_config(args.config)
    r.ensure_time(config)
    if args.send and not config.get('feishu_webhook'):
        raise r.Failure('历史测试发送前请配置飞书Webhook')
    source = r.Source(config)
    items = []
    for uid in config['uids']:
        batch = fetch_day(source, day.isoformat(), uid)
        items.extend(batch)
        print(f'读取 {uid}：{len(batch)}条；不写入正式数据库')
    from media_feishu import extract_images, ImageClient
    if args.images_only:
        items = [item for item in items if extract_images(item)]
    if args.replies_only:
        items = [item for item in items if r.message_parts(item, day.isoformat()).get('quote')]
    samples = select_samples(items, args.limit)
    if not samples:
        raise r.Failure('该日期没有目标用户消息可测试')
    # 历史测试仅使用内存上传缓存，不打开正式监听数据库。
    import sqlite3
    image_db = sqlite3.connect(':memory:')
    image_client = ImageClient(config, image_db) if args.send else None
    for index, item in enumerate(samples, 1):
        kind = category(item)
        body = f'【历史格式测试 {index}/{len(samples)}】\n日期：{day.isoformat()} · 样本：{kind}\n\n' + r.render(item, day.isoformat())
        if args.send:
            images = extract_images(item)
            parts = r.message_parts(item, day.isoformat())
            parts['test_label'] = f'【历史格式测试 {index}/{len(samples)}】 {day.isoformat()}'
            if images or parts.get('quote'):
                image_client.send(body, images, parts=parts)
            else:
                r.feishu(config, body)
            print(f'已发送 {index}/{len(samples)}：{kind}；飞书code=0')
            time.sleep(1)
        else:
            print(body, '\n')
    print('正式监听日期、历史基线、待发送队列均未修改。')
    image_db.close()


if __name__ == '__main__':
    try:
        main()
    except r.Failure as exc:
        print(str(exc))
        raise SystemExit(1)
    except KeyboardInterrupt:
        print('历史测试已停止')
    except Exception:
        print('历史测试发生本地异常，已停止；未输出原始异常或凭据。')
        raise SystemExit(1)
