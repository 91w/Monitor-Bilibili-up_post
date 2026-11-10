"""USB or mutually authenticated TLS configuration upload. Never prints credentials."""
import argparse, json, ssl, http.client, subprocess
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--config',default=r'D:\bWebMonitor\b-up\outputs\config.json')
p.add_argument('--host',help='Board LAN IP; omit to use USB')
p.add_argument('--pairing',default=r'D:\CodexProjects\work\b-up-reliable\pairing')
p.add_argument('--adb',default=r'D:\firefox\AllwinnertechPhoeniSuitRelease20201225.zip\AllwinnertechPhoeniSuitRelease20201225\adb.exe')
a=p.parse_args()
raw=Path(a.config).read_bytes(); json.loads(raw.decode('utf-8-sig'))
if a.host:
    pair=Path(a.pairing)
    ctx=ssl.create_default_context(cafile=str(pair/'ca.pem'))
    # The paired private CA issues only this board's server certificate;
    # certificate SAN uses a stable name while DHCP IP may change.
    ctx.check_hostname=False
    ctx.load_cert_chain(str(pair/'client.pem'),str(pair/'client.key'))
    conn=http.client.HTTPSConnection(a.host,8443,context=ctx,timeout=30)
    conn.request('POST','/config',body=raw,headers={'Content-Type':'application/json'})
    response=conn.getresponse(); body=response.read().decode()
    if response.status!=200: raise SystemExit(body)
    print(body)
else:
    subprocess.run([a.adb,'push',a.config,'/overlay/b-up/data/config.upload.json'],check=True)
    subprocess.run([a.adb,'shell','/overlay/b-up-runtime/python/bin/python3 /overlay/b-up/app/maintenance.py /overlay/b-up/data/config.upload.json && rm /overlay/b-up/data/config.upload.json'],check=True)
    print('Configuration saved; watcher will reload. Queue retained.')
