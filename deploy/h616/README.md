# H616 / Tina Linux 部署适配

这组文件已在 Yuzuki Chameleon H616、Tina Linux、ARM32 hard-float 用户环境及 Python 3.11 上部署验证。根目录 Windows 入口继续保留原行为；持久化队列由此目录 runner.py 启用，并非直接运行根目录脚本就会生效。

## 布局与启动

- Python：`/overlay/b-up-runtime/python/bin/python3`。
- 程序：根目录 bilibili_feishu_watcher.py 及本目录 runner.py、reliable.py、timeguard.py、maintenance.py 放入 `/overlay/b-up/app`。
- 私密配置与状态：`/overlay/b-up/data/config.json`、`state.json`。从 config.example.json 创建自己的配置，首次迁移需复制旧 state.json，保留基准避免重复。目录权限 700，文件 600。
- 日志目录：`/overlay/b-up/logs`。
- b-up-service、b-upctl、b-up-clock、b-up-ntp-hook 放 `/usr/sbin` 并授予执行权限。
- 系统需已支持 Wi-Fi、wpa_cli、DHCP、BusyBox ntpd；本目录不包含通用刷机镜像、无线驱动或 Python 二进制。

在 `/etc/inittab` 中按设备现有配置添加 init respawn（先备份，勿覆盖原内容）：

```text
null::respawn:/usr/sbin/b-up-clock
null::respawn:/usr/sbin/b-up-service
null::respawn:/overlay/b-up-runtime/python/bin/python3 /overlay/b-up/app/maintenance.py
```

校时通过才会启动，使用 `b-upctl start` 启用，`b-upctl stop` 停止，`b-upctl status`、`b-upctl logs` 检查。开机自动联网需另行部署和验证。

## 持久化、重试与历史恢复

`outbox.sqlite3` 保存新版权威进度与消息内容，首次启动只导入尚不存在的旧 state 基准。队列与游标事务提交后才发送；UID 与动态 ID 联合去重。失败按 30 秒指数退避到一小时，持续重试，发送成功后确认。运行进程每 30 秒检查一次队列；采集/网络耗时会延后重试。用户原 interval_seconds 是采集间隔，维护工具将它保存为 poll_interval_seconds，进程 interval_seconds 设为 30。

历史请求沿 offset 翻页，覆盖旧基准或无下一页时提交结果；遇到错误、游标循环或页数上限保留旧进度。默认 100 页，recovery_max_pages 可设 1–500。新增 UP 默认从当前动态建立基准，不补发全部历史；移除 UP 不取消已经入队的消息。

NTP 记录超过 30 分钟或时钟跳变超过 5 秒暂停发送，待自动校时后恢复。队列内容含通知正文，应限制设备访问。不要删除数据库或直接拿旧 state.json 覆盖新版进度。

无法保证绝对不漏不重：上游删除、权限变化、接口不返回历史、页数上限及存储损坏都可能影响完整性；飞书收到但应答丢失、发送后确认前进程终止可能重复。实机单测通过，不代表完成长期断网、断电或 24 小时稳定性验收。

## 同局域网配置更新

maintenance.py 提供 8443 HTTPS 接口，强制客户端证书。自行配置专用 CA 与服务器/客户端证书，服务器证书需 SERVER_AUTH，客户端证书需 CLIENT_AUTH，并包含常规 CA、KeyUsage、SKI、AKI 扩展。开发板 `/overlay/b-up/private` 中放 server.pem、server.key、ca.pem，限制为 root 访问。客户端配对目录放 client.pem、client.key、ca.pem。仓库不提供真实私钥或自动公开配对材料。

客户端：

```text
python update-config.py --config <配置路径> --host <开发板IP> --pairing <客户端配对目录>
python update-config.py --config <配置路径> --adb <adb可执行文件路径>
```

脚本默认路径针对原部署电脑，其他电脑应指定参数。图形入口 update-config-gui.py 也使用原电脑默认配置路径和配对目录，IP 可编辑。

客户端信任专用 CA，并关闭 DHCP 地址的主机名比对：该 CA 必须仅向这一台开发板签发服务器证书，勿复用组织通用 CA。无线服务不要映射到公网；同 Wi-Fi 若开启访客隔离也不能通信。更新验证 JSON 和必要字段，保存前一版并原子替换配置，然后请求监听进程退出重载，保留队列和状态。配置应用成功不等于 Cookie 权限已验证。

## 测试

在本目录执行 `python -m unittest test_reliable.py test_timeguard.py`。队列测试隔离使用临时数据库，覆盖持久化去重、退避重试、失败事务、分页恢复与分页上限，不向真实群发送通知。开发板还实测通过双向 TLS 上传、无客户端证书拒绝及无效配置拒绝。
