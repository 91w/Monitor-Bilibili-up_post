# �����Դ�븴�÷�Χ

�ο��û���Ŀ https://github.com/91w/Monitor-Bilibili-up_post ����ȡ�汾Ϊ `14c134bdce532c728ddb859faa2df27aba444d01`��

- `feishu_transport.py` �� `feishu_signature` ֱ�Ӹ��ø�Ŀ¼ `bilibili_feishu_watcher.py` ��ͬ��������
- `timeguard.py` ֱ�Ӹ��� `deploy/h616/timeguard.py`������ `/var/run/b-up-time-ready` ����ʱ���ǡ�
- `h616/runner.py` �������� runner ����־�ֻ��ͷ���ǰʱ���鷽ʽ��
- `h616/yyscha-service`��`h616/yyschactl` �ı��� `deploy/h616/b-up-service` �� `b-upctl`��ʹ�ö���Ŀ¼��PID���������ñ�ǡ�
- SQLite �������� `reliable.py` ��������ӡ�ʧ���˱ܡ�ȷ�ϳɹ����÷���״̬����ƣ���ҳ�����ںͷ�ҳģ�Ͳ�ͬ�����û��ֱ������ Bվ��Ϣ�α�����ݿ⡣
- `media_feishu.py` �ı��� `deploy/h616/dynamic_images.py` ��Ӧ��token��ͼƬ�ϴ����漰ͼ�Ŀ�Ƭ���̣����ñ�׼��HTTP��������yyscha��Դ��������Cookie��ȡ��ǩ��ˢ�£����ſ�ԭBվ��ͼƬ��������
- ��ͼ��Ϣʹ��������Ϣ����ͼ��Ϣʹ��ͼ�Ŀ�Ƭ���������ú�@���ϸ�Ҫ��JSON `code=0`������ȱʧҵ������Ϊ�ɹ�������������ԭʼ�����ƾ�ݡ�

���� Apache-2.0 ����������� LICENSE��ԭ��Ŀ����ʹ�õĴ��롢���ú����ݿ�δ�޸ģ�ͼƬ�����ڱ���Ŀ����ʵ�֡�
