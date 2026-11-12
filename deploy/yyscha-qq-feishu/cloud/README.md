# OSS 开通与验收准备

当前只提供待填权限模板，没有创建云资源、生成云访问密钥或验证线上权限。

使用新建、独立的私有标准存储桶，按量付费。开通前确认地域和控制台计费项目；记录实际价格、费用提醒接收人和阈值。不开通 ECS、CDN、域名、网站托管或日志服务。默认不启用版本控制、跨区域复制或额外访问日志；若已有组织要求启用，应单独核对其保留与费用。设置阻止公共访问，保持对象继承私有 ACL。

资源结构：`devices/设备标识/inbox/包ID.json` 保存密文，`devices/设备标识/receipts/包ID.json` 保存可认证回执。设备标识只使用小写字母、数字和连字符，不含账号、UID、Cookie 或其他个人信息。

创建两个独立 RAM 程序访问身份；只绑定对应的自定义策略，不叠加 AliyunOSSFullAccess 或任何其他权限。模板内的 `REPLACE_BUCKET` 和 `REPLACE_DEVICE` 必须替换为最终值。上传端只能写本设备 inbox 和读取本设备 receipts；板端只能列举/读取/删除本设备 inbox、写本设备 receipts。权限不能用于云账户管理、改 ACL、改生命周期或读取其他设备。长期密钥只通过本机私密配置；如采用 STS，需另行落实短期凭据的安全刷新来源。

使用管理身份为本项目两个前缀设置 3 天到期删除规则，作为漏清理兜底；这是云侧保留期，加密包本身有效期仍为 24 小时。成功应用并取得持久化回执后，领取器才删除 inbox 对象。上传成功不代表应用成功。回执只包含设备、包 ID、版本、包摘要和固定结果，不带 Cookie 或原始错误。

真实 Cookie 上传前，必须用合成内容完成以下线上验收：匿名 GetObject 拒绝；上传端写其他设备、读取 inbox、写 receipts、列桶根目录均拒绝；板端写 inbox、读取其他设备和改 Bucket 配置均拒绝；合法本设备操作通过；HTTP 拒绝、HTTPS 正常；篡改包不能应用。这里只是设计模板，必须以真实返回结果验收，不能把静态检查当作实际授权生效。

参考：[目录级 RAM 授权](https://help.aliyun.com/en/oss/user-guide/access-control-base-on-ram-policy)、[仅 HTTPS 的显式拒绝](https://help.aliyun.com/en/oss/tutorial-use-a-ram-policy-to-allow-a-user-to-access-oss-resources-only-over-https)。
