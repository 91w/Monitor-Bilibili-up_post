import os,signal,json,sys
from pathlib import Path
group=Path('/overlay/napcat-lab/cgroup/napcat-test')
if len(sys.argv)>1:
    active=group.parent.parent/'lab-active.json'
    if not active.exists() or str(json.loads(active.read_text()).get('pid'))!=sys.argv[1]:raise SystemExit('This test already ended; no other test stopped')
for pid in (group/'tasks').read_text().split():
    try:os.kill(int(pid),signal.SIGTERM)
    except ProcessLookupError:pass
print('Requested stop only for processes in dedicated test cgroup')
