"""Fail-closed OneBot event filter. Apply only to authenticated NapCat events."""
def numeric_id(value):
    if isinstance(value,bool):return None
    value=str(value) if value is not None else ''
    return value if value.isdecimal() and int(value)>0 else None

def should_forward(event,allowed_group_ids,include_owner=False):
    if not isinstance(event,dict) or event.get('post_type')!='message' or event.get('message_type')!='group':return False
    group=numeric_id(event.get('group_id'))
    allowed={numeric_id(g) for g in allowed_group_ids}
    if group is None or group not in allowed:return False
    if event.get('anonymous'):return False
    sender=event.get('sender')
    if not isinstance(sender,dict) or numeric_id(sender.get('user_id')) is None:return False
    roles={'admin','owner'} if include_owner else {'admin'}
    return sender.get('role') in roles
