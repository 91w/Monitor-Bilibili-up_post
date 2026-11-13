# Bilibili 动态监听 + 飞书群机器人通知

一个本地运行的 Python 脚本，用来监听指定 B 站 UP 主的最新动态，并把新动态内容发送到飞书群机器人。

支持文本动态、图文动态

## 功能

- 监听一个或多个 B 站 UP 主动态
- 新动态发送到飞书群机器人
- 支持飞书机器人关键词校验和签名校验
- 使用 `state.json` 记录已处理动态，避免重复通知
- 支持 `--dry-run` 本地预览，不实际发送飞书
- 支持 Windows 任务计划：每天定时启动、定时停止

## 文件说明

```text
bilibili_feishu_watcher.py     主脚本
config.example.json            配置模板
config.json                    本地配置，需自行创建
state.json                     运行后自动生成，记录每个 UP 的最新动态 ID
install_scheduled_tasks.ps1    Windows 定时启动/停止任务安装脚本
uninstall_scheduled_tasks.ps1  Windows 定时任务卸载脚本
requirements.txt               Python 依赖
```

## 准备飞书机器人

1. 在飞书群里添加“自定义机器人”。
2. 复制机器人的 Webhook 地址，填入 `feishu_webhook`。
3. 如果开启了“关键词”安全设置，把关键词填入 `feishu_keyword`。
4. 如果开启了“签名校验”，把签名密钥填入 `feishu_secret`。

## 安装依赖

```powershell
python -m pip install -r .\requirements.txt
```

```powershell
python -m pip install requests
```

## 创建配置

复制模板：

```powershell
Copy-Item .\config.example.json .\config.json
```

编辑 `config.json`：

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
      "name": "示例UP主"
    }
  ]
}
```

字段说明：

- `interval_seconds`：长期运行时的检查间隔，单位秒，建议不少于 `300`。
- `notify_on_first_run`：首次运行时是否发送当前最新动态。正式运行建议设为 `false`。
- `bilibili_cookie`：可选。接口受限或需要登录态时填写浏览器里的 B 站 Cookie。
- `feishu_webhook`：飞书自定义机器人的 Webhook。
- `feishu_secret`：飞书机器人签名密钥；未开启签名校验则留空。
- `feishu_keyword`：飞书机器人关键词校验词；未开启关键词校验则留空。
- `up_users`：要监听的 UP 主列表。`uid` 是空间地址里的数字，例如 `https://space.bilibili.com/123456` 的 UID 是 `123456`。

## Windows 定时启动和停止

仓库提供了一键安装任务计划的脚本。默认每天 `08:00` 启动，`23:30` 停止。

```powershell
.\install_scheduled_tasks.ps1
```

自定义时间：

```powershell
.\install_scheduled_tasks.ps1 -StartAt "09:00" -StopAt "22:00"
```

手动测试启动任务：

```powershell
Start-ScheduledTask -TaskName "BilibiliFeishuWatcherStart"
```

手动测试停止任务：

```powershell
Start-ScheduledTask -TaskName "BilibiliFeishuWatcherStop"
```

卸载任务计划：

```powershell
.\uninstall_scheduled_tasks.ps1
```

## 常见问题

### 首次运行没有发送消息

如果 `notify_on_first_run` 是 `false`，首次运行只会初始化 `state.json`，不会发送当前已有动态。这是为了避免刚启动就推送旧消息。

### 飞书返回 `Key Words Not Found`

飞书机器人开启了关键词校验，但消息内容没有命中关键词。请填写：

```json
"feishu_keyword": "你的关键词"
```

### 收不到新动态

请检查：

- `up_users.uid` 是否正确
- `state.json` 是否已经记录了该 UP 的最新动态
- 飞书机器人 Webhook 和安全设置是否正确
- `bilibili_cookie` 是否过期



## H616 全天监听与无线维护

已增加动态配图（沿用原Webhook，应用上传图片）、局域网状态页、每日运行摘要与Windows路由器查询/三天IP缓存入口。部署步骤、凭据配置、图片资源限制与失败降级策略见 [开发板说明](deploy/h616/README.md)。不要上传实际config、Cookie、App Secret、证书私钥或运行数据库。

开发板适配入口与部署说明见 [deploy/h616](deploy/h616/README.md)：SQLite 持久化待发送队列、失败退避重试、历史分页补抓、NTP 发送保护，以及 USB / 局域网双向 TLS 配置更新。Windows 原入口保持原工作方式，使用 runner.py 才启用开发板队列。

## H616 QQ 管理员消息转发备份

QQ/NapCat 的源码、测试、存储维护与换板记录见 [QQ 转发部署目录](deploy/napcat-h616/README.md)。B站和QQ群是两个独立消息来源，当前部署共享可信时间和飞书图片上传代码。QQ 部分新增表情过滤、持久化连续发言合并，以及文字配图同条通知；75 项组件测试通过。

公开副本仅含源码和脱敏说明，不含密码、Webhook、Token、登录态、聊天数据库或运行队列。示例群号及 Windows USB 工具路径需按实际环境配置。

## yyscha 网页指定用户消息转发

独立的 [网页消息转发目录](deploy/yyscha-qq-feishu/README.md) 已更新至 v19：修复源站重新归档给旧消息分配新ID引发的历史重发，加入跨归档身份、归档内重复次数及疑似媒体隔离；保留轻量检测、卡片/图片、30 MB 文件、WAV语音附件、COS加密Cookie同步和服务启动提醒。生产核心157项测试通过，公开快照含迁移工具共160项测试通过。**旧数据库升级须先暂停、备份并按身份迁移说明处理，不能重建基线绕过保护。** 软件在板端独立运行，独立电源冷启动及长期观察仍待验收；不保证网络端严格恰好一次。公开代码不包含真实账号标识、凭据、私钥、Cookie、聊天正文或运行数据库。原 B站启动提醒及 NapCat 组件保留。
