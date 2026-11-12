# Bilibili ��̬���� + ����Ⱥ������֪ͨ

һ���������е� Python �ű�����������ָ�� B վ UP �������¶�̬�������¶�̬���ݷ��͵�����Ⱥ�����ˡ�

֧���ı���̬��ͼ�Ķ�̬

## ����

- ����һ������ B վ UP ����̬
- �¶�̬���͵�����Ⱥ������
- ֧�ַ�������˹ؼ���У���ǩ��У��
- ʹ�� `state.json` ��¼�Ѵ�����̬�������ظ�֪ͨ
- ֧�� `--dry-run` ����Ԥ������ʵ�ʷ��ͷ���
- ֧�� Windows ����ƻ���ÿ�춨ʱ��������ʱֹͣ

## �ļ�˵��

```text
bilibili_feishu_watcher.py     ���ű�
config.example.json            ����ģ��
config.json                    �������ã������д���
state.json                     ���к��Զ����ɣ���¼ÿ�� UP �����¶�̬ ID
install_scheduled_tasks.ps1    Windows ��ʱ����/ֹͣ����װ�ű�
uninstall_scheduled_tasks.ps1  Windows ��ʱ����ж�ؽű�
requirements.txt               Python ����
```

## ׼�����������

1. �ڷ���Ⱥ�����ӡ��Զ�������ˡ���
2. ���ƻ����˵� Webhook ��ַ������ `feishu_webhook`��
3. ��������ˡ��ؼ��ʡ���ȫ���ã��ѹؼ������� `feishu_keyword`��
4. ��������ˡ�ǩ��У�顱����ǩ����Կ���� `feishu_secret`��

## ��װ����

```powershell
python -m pip install -r .\requirements.txt
```

```powershell
python -m pip install requests
```

## ��������

����ģ�壺

```powershell
Copy-Item .\config.example.json .\config.json
```

�༭ `config.json`��

```json
{
  "interval_seconds": 300,
  "notify_on_first_run": false,
  "bilibili_cookie": "",
  "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "feishu_secret": "",
  "feishu_keyword": "",
  "up_users": [
    {
      "uid": "2",
      "name": "ʾ��UP��"
    }
  ]
}
```

�ֶ�˵����

- `interval_seconds`����������ʱ�ļ��������λ�룬���鲻���� `300`��
- `notify_on_first_run`���״�����ʱ�Ƿ��͵�ǰ���¶�̬����ʽ���н�����Ϊ `false`��
- `bilibili_cookie`����ѡ���ӿ����޻���Ҫ��¼̬ʱ��д�������� B վ Cookie��
- `feishu_webhook`�������Զ�������˵� Webhook��
- `feishu_secret`�����������ǩ����Կ��δ����ǩ��У�������ա�
- `feishu_keyword`����������˹ؼ���У��ʣ�δ�����ؼ���У�������ա�
- `up_users`��Ҫ������ UP ���б���`uid` �ǿռ��ַ������֣����� `https://space.bilibili.com/123456` �� UID �� `123456`��

## Windows ��ʱ������ֹͣ

�ֿ��ṩ��һ����װ����ƻ��Ľű���Ĭ��ÿ�� `08:00` ������`23:30` ֹͣ��

```powershell
.\install_scheduled_tasks.ps1
```

�Զ���ʱ�䣺

```powershell
.\install_scheduled_tasks.ps1 -StartAt "09:00" -StopAt "22:00"
```

�ֶ�������������

```powershell
Start-ScheduledTask -TaskName "BilibiliFeishuWatcherStart"
```

�ֶ�����ֹͣ����

```powershell
Start-ScheduledTask -TaskName "BilibiliFeishuWatcherStop"
```

ж������ƻ���

```powershell
.\uninstall_scheduled_tasks.ps1
```

## ��������

### �״�����û�з�����Ϣ

��� `notify_on_first_run` �� `false`���״�����ֻ���ʼ�� `state.json`�����ᷢ�͵�ǰ���ж�̬������Ϊ�˱�������������;���Ϣ��

### ���鷵�� `Key Words Not Found`

��������˿����˹ؼ���У�飬����Ϣ����û�����йؼ��ʡ�����д��

```json
"feishu_keyword": "��Ĺؼ���"
```

### �ղ����¶�̬

���飺

- `up_users.uid` �Ƿ���ȷ
- `state.json` �Ƿ��Ѿ���¼�˸� UP �����¶�̬
- ��������� Webhook �Ͱ�ȫ�����Ƿ���ȷ
- `bilibili_cookie` �Ƿ����



## H616 ȫ�����������ά��

�����Ӷ�̬��ͼ������ԭWebhook��Ӧ���ϴ�ͼƬ����������״̬ҳ��ÿ������ժҪ��Windows·������ѯ/����IP������ڡ������衢ƾ�����á�ͼƬ��Դ������ʧ�ܽ������Լ� [������˵��](deploy/h616/README.md)����Ҫ�ϴ�ʵ��config��Cookie��App Secret��֤��˽Կ���������ݿ⡣

��������������벿��˵���� [deploy/h616](deploy/h616/README.md)��SQLite �־û������Ͷ��С�ʧ���˱����ԡ���ʷ��ҳ��ץ��NTP ���ͱ������Լ� USB / ������˫�� TLS ���ø��¡�Windows ԭ��ڱ���ԭ������ʽ��ʹ�� runner.py �����ÿ�������С�

## H616 QQ ����Ա��Ϣת������

QQ/NapCat ��Դ�롢���ԡ��洢ά���뻻���¼�� [QQ ת������Ŀ¼](deploy/napcat-h616/README.md)��Bվ��QQȺ������������Ϣ��Դ����ǰ����������ʱ��ͷ���ͼƬ�ϴ����롣QQ ��������������ˡ��־û��������Ժϲ����Լ�������ͼͬ��֪ͨ��75 ���������ͨ����

������������Դ�������˵�����������롢Webhook��Token����¼̬���������ݿ�����ж��С�ʾ��Ⱥ�ż� Windows USB ����·���谴ʵ�ʻ������á�

## yyscha ��ҳָ���û���Ϣת��

���������� [��ҳ��Ϣת��Ŀ¼](deploy/yyscha-qq-feishu/README.md)����UID��ҳ�ɼ���SQLite���������ԡ����ûҿ���ʵͼƬ�ϴ���Cookie�־ûỰ�ͱ��ص�¼�ָ����������Ȩ�����еı����ɼ�����鷢����֤������Ŀ��ʽH616Ǩ�ơ��ƶ˼�����ת�ͷ��鷴�������δ��ɡ��������ղ���ʵ��UID������ƾ�ݡ������¼���������ݿ⣻ԭBվ��NapCat�ļ�����ԭ����

