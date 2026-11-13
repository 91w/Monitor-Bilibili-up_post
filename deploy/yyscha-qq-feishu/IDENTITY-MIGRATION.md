# v19旧数据库升级

新数据库按正常基线流程初始化；已有消息库必须先暂停、备份并迁移。不要删除数据库、重建基线或覆盖上线后的发送状态。

1. 停止本项目服务，确认watcher已退出：`sh /overlay/yyscha/app/yyschactl stop`。使用SQLite backup API保留一致性数据库，同时备份配置、配对身份、会话和旧代码到私密目录，不能将凭据写入公开输出目录。
2. 解压新源码并准备代码更新，**暂不启动服务**。复制或合并config.example.json新增的布尔设置，启用require_source_record；保留原有真实配置及队列。运行时和可信CA/NTP依赖仍按README适配。
3. 在新包根目录运行默认试迁移：

```sh
python identity_migration.py --config /overlay/yyscha/data/config.json --db /overlay/yyscha/data/state.sqlite3
```

该命令锁定发送进程、读取配置UID和已有日期/聊天流、验证源站范围，取得完整身份快照，在私密临时数据库演练。只打印计数，不发送飞书，也不改原消息状态。源站缺少归档标识、服务仍启用或锁不可用时会停止。不要启动发送者与它并行运行。

4. 核对before/after、duplicates_isolated、unresolved_quarantined等计数，确认有备份后再显式应用：

```sh
python identity_migration.py --config /overlay/yyscha/data/config.json --db /overlay/yyscha/data/state.sqlite3 --apply
```

应用只回填消息身份、隔离已确认副本/当前源站缺失的未完成ID，并设置迁移标记；已发送与基线状态保留。不将隔离记录标为已发送。源码中的防护会阻止未迁移旧库启动；不要手动伪造迁移标记绕过。

5. 核对仍待发送/重试和隔离记录，再安装/启用新服务。对于不能确认的记录继续隔离，不能自动全部释放。恢复后先观察队列和去重状态，保留原备份用于追查。

以上示例针对已部署H616路径；其他环境请替换私密路径。迁移会对源站做一次完整核对，正常运行仍采用轻量变化检测。该工具不删除源站或飞书中的记录，也不自动撤回历史卡片。
