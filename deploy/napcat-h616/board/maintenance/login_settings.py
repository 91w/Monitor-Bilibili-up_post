"""Private password-fallback settings. Never put passwords in argv or stdout."""
import json,os,stat,sys,tempfile
from pathlib import Path
BASE=Path('/overlay/napcat-lab')

def validate(data,account):
    uin=data.get('account');password=data.get('password')
    if not isinstance(uin,str) or not uin.isascii() or not uin.isdecimal() or uin!=str(account):raise ValueError('Account must match current forwarding account')
    if not isinstance(password,str) or not 1<=len(password)<=256 or '\x00' in password:raise ValueError('Invalid password')
    return {'enabled':True,'account':uin,'password':password}

def save(base,data):
    base=Path(base);account=json.loads((base/'forward-config.json').read_text())['self_id']
    value=validate(data,account);folder=base/'private'
    if folder.is_symlink():raise ValueError('Unsafe credential directory')
    folder.mkdir(mode=0o700,exist_ok=True);folder.chmod(0o700)
    target=folder/'qq-login.json';temp=folder/'qq-login.new'
    if target.is_symlink() or temp.is_symlink():raise ValueError('Unsafe credential file')
    with temp.open('w',encoding='utf-8') as f:
        os.chmod(temp,0o600);json.dump(value,f,ensure_ascii=True);f.flush();os.fsync(f.fileno())
    temp.replace(target)
    fd=os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)

def environment_for_login(base,label,cmd,environ):
    env=dict(environ)
    if label!='qq-live':return env
    for k in ('NAPCAT_QUICK_PASSWORD','NAPCAT_QUICK_PASSWORD_MD5','NAPCAT_QUICK_ACCOUNT','ACCOUNT'):env.pop(k,None)
    base=Path(base);path=base/'private/qq-login.json'
    if not path.exists():return env
    try:
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077 or info.st_uid!=os.geteuid():return env
        account=str(json.loads((base/'forward-config.json').read_text())['self_id'])
        if '/opt/QQ/qq' not in cmd or '-q' not in cmd or cmd[cmd.index('-q')+1]!=account:return env
        data=json.loads(path.read_text())
        if not data.get('enabled'):return env
        data=validate(data,account)
        env.update(ACCOUNT=account,NAPCAT_QUICK_ACCOUNT=account,NAPCAT_QUICK_PASSWORD=data['password'])
    except (OSError,ValueError,KeyError,IndexError):return env
    return env

def main():
    os.umask(0o077);action=sys.argv[1] if len(sys.argv)>1 else 'status';p=BASE/'private/qq-login.json'
    if action=='status':
        c=json.loads((BASE/'forward-config.json').read_text());print(json.dumps({'account':str(c['self_id']),'configured':p.is_file() and not p.is_symlink()}))
    elif action in ('save','protocol-test'):
        # Old ADB shell transports do not reliably propagate stdin EOF.
        # A newline-delimited JSON request finishes without waiting for EOF.
        raw=sys.stdin.buffer.readline(8193)
        if len(raw)>8192:raise ValueError('Input limit')
        data=json.loads(raw)
        if action=='save':save(BASE,data);print('QQ_PASSWORD_SETTINGS_SAVED')
        else:
            account=json.loads((BASE/'forward-config.json').read_text())['self_id']
            with tempfile.TemporaryDirectory(prefix='credential-protocol-test-',dir=BASE) as folder:
                folder=Path(folder);(folder/'forward-config.json').write_text(json.dumps({'self_id':account}))
                save(folder,data)
                env=environment_for_login(folder,'qq-live',['/opt/QQ/qq','-q',str(account)],{})
                if env.get('NAPCAT_QUICK_PASSWORD')!=data['password']:raise ValueError('Environment mismatch')
            print('QQ_PASSWORD_SETTINGS_PROTOCOL_OK')
    elif action=='clear':
        if p.is_symlink():raise ValueError('Unsafe credential file')
        p.unlink(missing_ok=True);print('QQ_PASSWORD_SETTINGS_CLEARED')
    else:raise ValueError('Unknown action')
if __name__=='__main__':
    try:main()
    except Exception:print('QQ_PASSWORD_SETTINGS_FAILED',file=sys.stderr);raise SystemExit(1)
