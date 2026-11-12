# yyscha 指定用户消息 → 飞书

读取用户已获授权的 yyscha 网页聊天记录，按当前发言者 UID 筛选新消息，分离引用与回复，转发文字和图片到飞书群。它读取网站记录，时效取决于网站同步速度，不是直接登录QQ接收消息。

这是已验证的本地采集转发和会话维护源码快照，Python 3.11+，核心仅依赖标准库。未包含尚在开发的云中转／加密包组件、实际配置或运行数据。原仓库B站与NapCat程序不受影响。

## 已完成的能力

- 逐UID请求消息接口，校验业务结果、日期、聊天范围和返回UID；无需先调用check_login。
- 默认约15秒检查，total快照分页、重叠读取、周期全量对账和跨天补抓。
- SQLite首次历史基线、站点/聊天范围/消息ID去重、事务入队、发送确认与失败退避；重启保留状态。
- 解析HTML正文、@、引用作者和网页提供的引用内容。引用放灰底卡片，回复独立显示；引用/回复配图区分。QQ表情保留文字含义，头像不视为配图。
- 带登录态下载固定来源图片，上传飞书取得image_key后同条展示；图片失败保留重试。
- 站点专用CookieJar持久化Set-Cookie、期限及删除指令，重启读回；配置更新后运行中的新版监听自动重读。
- 登录失效时保留游标并通过既有Webhook限频提醒；本地恢复网页支持人工填写图形验证码登录，或验证后导入cookies.txt。

真实Cookie读取、UID筛选、分页、历史基线、飞书文字/引用/图片发送，以及Cookie响应更新与重启读回均已验证。完整密码登录、自然失效后恢复、长期稳定性与本项目正式H616迁移仍需用户环境验收，详见 [VALIDATION.md](VALIDATION.md)。

## 本地运行

进入本目录；Windows也可使用数字编号CMD入口。示例UID均为占位值，必须填写自己的监听对象。凭据只填本机配置，不提交仓库。

```powershell
python settings.py
python relay.py check
python relay.py preview
python relay.py once
python relay.py test-send
python relay.py run --send
```

`test-send` 会向配置的飞书群发送一条明确标注的模拟测试消息。首次 `once` 或 `run` 仅建立昨日和今日基线；以后 `once` 会采集新增入队。没有 `--send` 的 `run` 仅采集；之后启用发送会处理积累的待发送记录。停止运行按Ctrl+C。

```powershell
python relay.py status
python relay.py session-status
python history_test.py --day YYYY-MM-DD
python history_test.py --day YYYY-MM-DD --replies-only --images-only --limit 2 --send
python recovery_web.py --open-browser
```

历史测试仅在内存处理，不更改正式消息数据库；每次显式使用 `--send` 都可能再次发送样本。`preview` 为首个页面中的样本，不保证是当天最新消息。

Windows默认私密目录为用户主目录下 `.yyscha-qq-feishu`；板端默认为 `/overlay/yyscha/data`。可在启动前设置环境变量 `YYSCHA_PRIVATE_DIR`，也可用 `--config` 和 `--db` 指定路径。目录包含 `config.json`、消息数据库和 `site-session.sqlite3`，都不应提交。

配置字段见 [config.example.json](config.example.json)。Cookie可由本地Netscape cookies.txt导入；飞书Webhook负责发送，配图另需已发布应用的App ID/Secret和图片上传权限。不要把现有私密配置复制到仓库根目录。

## 登录恢复与远程边界

恢复网页只监听 `127.0.0.1:8766`，带随机路径并核验Host/Origin。通过网站正常CSRF和验证码流程登录；用户填写验证码，密码不保存。候选会话先隔离验证消息读取，成功才更新配置，不重置基线或队列。

电脑启动的恢复网页只更新电脑；板端启动的恢复网页才更新板端。仅打开电脑页面不会把Cookie同步到家中设备。远程访问板端页面需要已建立的加密网络和同端口SSH转发；本快照未提供已部署的外网通道。

现有群自定义Webhook用于通知，本快照未开通飞书反向接收，也未包含对象存储加密中转。Cookie服务器期限不代表无限续期，账号退出、会话撤销或验证码要求仍可能需要人工恢复。

## 图片与可靠性限制

图片仅从已验证的 `https://www.yyscha.cn/application/QQ/serve.php` 读取，携带站点Cookie，不跟随重定向或向其他域转发Cookie。验证文件类型及9MiB上限，默认每条最多6张。音频和视频仍以提示展示。

引用内容可能由网页截断，不保证完整原文。未知或未附图片不会虚称下方有图。请求使用直接连接，不继承环境HTTP代理；需要代理的网络需另行适配。

失败重试可能改变显示顺序；网络超时或远端发送成功后本地提交前崩溃可能重复通知。周期重叠读取不能证明网站历史插入永不遗漏；停止复查的旧日期可能需要人工对账。数据库长期容量策略仍需实际运行观察。

## H616准备文件

[h616/install.sh](h616/install.sh) 只复制本项目程序，不启用服务、不覆盖既有配置和消息数据库。复用现有 `/overlay/b-up-runtime/python/bin/python3`、可信CA文件和NTP标记 `/var/run/b-up-time-ready`；不是通用Linux一键安装器。需先检查32位ARM用户环境、Python3.11+的ssl/sqlite3、可信证书及目录权限。

```sh
sh h616/install.sh
# 安全配置 /overlay/yyscha/data/config.json，开启 require_trusted_time
sh /overlay/yyscha/app/yyschactl collect
sh /overlay/yyscha/app/yyschactl status
# 用户环境验收后明确启用正式发送
sh /overlay/yyscha/app/yyschactl start
```

迁移需先停用旧发送进程，并用SQLite backup API生成一致性快照；不要复制正在写入的WAL数据库主文件来代替备份。保持一个正式发送者。H616脚本已准备，本项目尚未完成正式板端迁移和全天验证。

## 测试与许可

```sh
python -m unittest discover -v
```

测试使用合成消息、模拟网络与临时数据库，不要求真实Cookie，不应发送飞书或连接真实网站。测试结束清理本目录 `.test-work` 中自己创建的目录。

复用组件及Apache-2.0许可见 [NOTICE.md](NOTICE.md) 与 [LICENSE](LICENSE)。
