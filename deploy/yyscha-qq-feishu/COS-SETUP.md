# COS 设置

1. 在两端设置相同 `YYSCHA_COS_BUCKET`、`YYSCHA_COS_REGION`；公开版默认桶名是假数据。
2. 创建私有读写桶，使用 cloud 中模板并替换 APPID、桶名、地域。分别创建电脑上传和板端领取身份，仅允许本设备 inbox/receipts 的必要 HTTPS 操作；领取端列举前缀按 URL 编码形式配置。
3. 用 `python cos_settings.py` 在本机私密目录录入两端云凭据；首次将领取端配置经可信通道送到板端，完成比对后清理电脑 bootstrap 暂存。日常电脑仅保留上传端凭据。
4. 完成 SECURE-PACKAGE 中首次身份配对；按设备前缀设置生命周期清理和账户费用提醒。
5. 显式运行 `python cos_probe.py` 执行权限检查：会向云端上传合成测试密文及回执，不读取真实 Cookie，不发送飞书。仅明确 AccessDenied 的 403 才计为预期拒绝。
6. 日常 `python recovery_web.py --open-browser` 登录后验证并加密上传，`python update_agent.py sync` 查询与重试；成功以认证后的应用回执为准。

云凭据撤销不等于配对私钥轮换。不要把配置、身份文件、Cookie、日志和运行数据库提交到 GitHub。桶费用按实际请求、存储、流量结算。
