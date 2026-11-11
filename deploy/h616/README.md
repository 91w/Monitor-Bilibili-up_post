# H616 / Tina Linux 部署适配

2026-11-11 更新：动态图文转发、局域网状态与网页配置、飞书运行日报，以及路由器查询＋三天IP缓存的Windows入口已实现。见下方“新增功能”。个人凭据、证书、MAC、数据库和日志不在仓库内。

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

配置默认位于仓库根config.json，可通过B_UP_CONFIG_PATH覆盖。配对资料默认在用户目录.b-up/pairing，可通过B_UP_PAIRING_DIR或命令行--pairing覆盖；USB模式使用PATH中的adb或B_UP_ADB指定的程序。图形入口的IP由用户填写。

客户端信任专用 CA，并关闭 DHCP 地址的主机名比对：该 CA 必须仅向这一台开发板签发服务器证书，勿复用组织通用 CA。无线服务不要映射到公网；同 Wi-Fi 若开启访客隔离也不能通信。更新验证 JSON 和必要字段，保存前一版并原子替换配置，然后请求监听进程退出重载，保留队列和状态。配置应用成功不等于 Cookie 权限已验证。

## 测试

在本目录执行 `python -m unittest test_reliable.py test_timeguard.py`。队列测试隔离使用临时数据库，覆盖持久化去重、退避重试、失败事务、分页恢复与分页上限，不向真实群发送通知。开发板还实测通过双向 TLS 上传、无客户端证书拒绝及无效配置拒绝。

## 新增功能

### 动态配图

继续使用原Webhook机器人发送卡片；新建飞书自建应用、开启机器人能力及 `im:resource`，发布后在私密config中填写feishu_app_id、feishu_app_secret，并将enable_dynamic_images设为true。新增字段示例在image-config.example.json，必须合并到完整配置，不能拿示例覆盖整个config。

dynamic_images.py提取draw、opus、段落及旧格式图片，也处理orig中的配图。逐张下载到临时文件，只接受指定B站CDN HTTPS地址，校验重定向；不把B站Cookie传到图片CDN。单图限制9MiB，默认一条最多18张，max_dynamic_images可设1–30。飞书访问令牌仅在内存缓存，成功上传的图片标识在SQLite保存7天。

队列新增images_json与media_failures列，旧记录保持可读；图片元数据与抓取进度一起提交。连续三次图片准备失败后，下次发送正文、可用图片及缺图说明，不再单独补发缺失图片。超过展示上限也明确说明。Webhook网络发送失败继续原有重试，不触发图片降级。受限或接口不提供的图片无法绕过权限。历史已发动态不批量重发。

Linux应用目录需新增dynamic_images.py，并更新reliable.py、runner.py及maintenance.py。先正常停止监听，备份配置并用SQLite backup接口备份队列，然后原子替换程序文件、合并应用凭据、重新启动；不要拷贝其他设备的数据库覆盖自己的进度。runner.py增加App Secret脱敏，网页维护验证新增字段。

### 状态页、网页配置与日报

将status_service.py、config_page.html及test_status.py复制到Linux应用目录，在现有inittab中追加：

```text
null::respawn:/overlay/b-up-runtime/python/bin/python3 /overlay/b-up/app/status_service.py
```

http://开发板IP:8080/ 是只读状态页，每15秒刷新，显示进程、校时、Wi-Fi、UP最近检查、空列表/失败结果、队列和24小时计数。读取失败提示过期，不返回Cookie、应用Secret、webhook或原始日志。它未提供登录，仅供受信任局域网使用；不要映射公网。接口空列表不等于确认没有动态。

https://开发板IP:8443/ 提供config.json文件上传页面，沿用双向TLS客户端证书，校验Origin，原子替换配置并请求进程重载。用户需要信任专用CA与安装客户端证书；直接浏览器访问时服务器证书SAN必须覆盖实际IP或主机名。

日报服务与状态页同进程，由设备init拉起。默认北京时间21点，首次从启用后的下一个21点开始，统计实际生成时过去24小时的采集、空列表、发送、重试、积压及资源情况。daily_report_enabled=false可停用，daily_report_hour可设0–23。报告持久化、成功后确认，失败退避重试；恢复时可补最近到点摘要，不为全部离线日期伪造历史日报。设备断电或完全断网时无法自行报告失联，仍需外部心跳补足。指标保留35天，自升级开始计数。

### Windows网页入口与三天地址缓存

home-web-launcher.py启动时打开路由器管理页，提示用户按MAC查IP，用户输入后校验设备并打开本机状态/配置页。首次成功记住IP三天，期间不顺延；每次启动仍校验设备身份，地址变动时回到查表流程。三天是工具缓存期限，不代表DHCP租约保证IP绝不变化。工具不读取路由器密码或Cookie。

公开源码不包含实际MAC、内网地址或个人目录。使用前设置B_UP_BOARD_MAC为设备Wi-Fi MAC、B_UP_ROUTER_URL为路由器管理网址；未配置时显示示例MAC和router.local。缓存默认位于用户目录.b-up，可用B_UP_STATE_DIR覆盖。配对目录由B_UP_PAIRING_DIR或--pairing指定。启动CMD使用PATH中的pythonw.exe，需安装含Tk的Python并加入PATH。

local-config-web.py仅监听127.0.0.1的随机端口，以随机路径、Host与Origin限制请求。浏览器访问本机HTTP，配置由工具通过双向TLS并核对配对服务器证书SHA256指纹后上传，未关闭远端证书链验证。允许DHCP IP变化，所以不依赖远端IP与证书SAN相同；配对目录需要server.pem、client.pem、client.key、ca.pem。固定的是具体设备证书，不是“忽略所有证书错误”。换证或证书过期后需维护配对资料。

image-settings.py提供本机凭证填写窗口，默认config为仓库根config.json，可用B_UP_CONFIG_PATH环境变量覆盖；更新前保留config.before-images.json，已加入忽略规则。凭证不得写入例子、聊天、日志或GitHub。

### 验证记录

开发板部署环境19项测试通过：原队列、时间保护、日报与9项配图测试；真实图片下载、上传及原Webhook验证卡片成功，用户确认图片显示正常，旧队列记录和游标未回退。Linux集成测试依赖已准备的/overlay/b-up目录与配置；Windows不能直接运行需要time.tzset和Linux目录的runner集成测试。

```text
# 已部署的开发板应用目录
python3 -m unittest test_reliable.py test_timeguard.py test_status.py test_dynamic_images.py
# Windows本目录：IP缓存与输入校验
python -m unittest test_home_launcher.py
```

单元测试的HTTP请求是mock，不发送真实通知；真实验证仅发送过一条带明确标记的配图验证卡。下一条实际动态的完整自动转发及长期稳定性仍应持续观察，测试不构成永不漏报或重复的保证。
