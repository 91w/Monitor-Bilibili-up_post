# 组件来源与复用范围

参考用户项目 https://github.com/91w/Monitor-Bilibili-up_post ，读取版本为 `14c134bdce532c728ddb859faa2df27aba444d01`。

- `feishu_transport.py` 的 `feishu_signature` 直接复用根目录 `bilibili_feishu_watcher.py` 中同名函数。
- `timeguard.py` 直接复用 `deploy/h616/timeguard.py`，沿用 `/var/run/b-up-time-ready` 可信时间标记。
- `h616/runner.py` 沿用现有 runner 的日志轮换和发送前时间检查方式。
- `h616/yyscha-service`、`h616/yyschactl` 改编自 `deploy/h616/b-up-service` 和 `b-upctl`；使用独立目录、PID、锁和启用标记。
- SQLite 队列沿用 `reliable.py` 的事务入队、失败退避、确认成功后置发送状态的设计；网页的日期和分页模型不同，因此没有直接套用 B站消息游标或数据库。
- `media_feishu.py` 改编自 `deploy/h616/dynamic_images.py` 的应用token、图片上传缓存及图文卡片流程；改用标准库HTTP、单独的yyscha资源白名单、Cookie读取及签名刷新，不放宽原B站的图片白名单。
- 无图消息使用文字消息，含图消息使用图文卡片，保留引用和@。严格要求JSON `code=0`，不把缺失业务码视为成功；不输出服务端原始错误或凭据。

上游 Apache-2.0 许可随包附于 LICENSE。原项目正在使用的代码、配置和数据库未修改；图片适配在本项目独立实现。
