"""Cheap metadata-only sticker filtering; never infer from GIF format or size."""
from urllib.parse import urlparse

def group_rule(cfg,group):
    return cfg.get('group_rules',{}).get(str(group),{})

def is_sticker(segment):
    kind=segment.get('type');data=segment.get('data') or {}
    if kind in ('face','mface','marketface'):return True
    if kind!='image':return False
    # NapCat v4.18.42 api/msg.ts maps marketFaceElement into image with these IDs.
    if data.get('emoji_id') or data.get('emoji_package_id'):return True
    if str(data.get('sub_type',''))=='1':return True  # PicSubType.KCUSTOM
    if data.get('summary') in ('[动画表情]','[表情]','[商城表情]'):return True
    try:
        url=urlparse(str(data.get('url','')))
        if url.hostname=='gxh.vip.qq.com' and url.path.startswith('/club/item/parcel/item/'):return True
    except ValueError:pass
    return False

def clean_segments(segments,rule):
    cleaned=[]
    for segment in segments:
        if not isinstance(segment,dict):continue
        data=segment.get('data') or {}
        if not isinstance(data,dict):continue
        if rule.get('drop_stickers') and is_sticker(segment):continue
        cleaned.append({'type':str(segment.get('type','unknown')),
                        'data':{k:data[k] for k in ('text','url','file','name','qq','id','data','summary') if k in data}})
    if rule.get('drop_stickers'):
        # A reply marker alone, or whitespace left after removing a sticker, is not content.
        meaningful=any(s['type'] not in ('text','reply') or
                       (s['type']=='text' and str(s['data'].get('text','')).strip()) for s in cleaned)
        if not meaningful:return []
    return cleaned

def plain_text(msg):
    if not msg['segments'] or any(s['type']!='text' for s in msg['segments']):return None
    return ''.join(str(s['data'].get('text','')) for s in msg['segments'])

def join_text(left,right):
    # Retain each fragment verbatim. Newlines preserve boundaries and avoid inventing a sentence.
    return left+'\n'+right
