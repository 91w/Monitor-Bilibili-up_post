"""Read-only live session probe; never prints source messages or cookie values."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relay as r
from site_session import SiteSession


def check(path):
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    session = SiteSession(config['site_cookie'])
    config['_site_session'] = session
    try:
        source = r.Source(config)
        day = (r.datetime.now(r.TZ).date() - r.timedelta(days=1)).isoformat()
        result = []
        for uid in config['uids']:
            page = source.page(day, uid, 0)
            result.append({'uid': uid, 'business_ok': True, 'sample_count': len(page['items']),
                           'total': page['total'], 'uid_verified': bool(page['items'])})
        print(json.dumps({'day': day, 'results': result}))
    except r.Failure as exc:
        print(json.dumps({'business_ok': False, 'reason': str(exc)}, ensure_ascii=True))
        return 1
    except Exception:
        print(json.dumps({'business_ok': False, 'reason': 'Unexpected probe failure; credentials suppressed'}))
        return 1
    finally:
        session.close()
        Path(path).unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    sys.exit(check(sys.argv[1]))
