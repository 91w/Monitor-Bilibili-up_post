# 加密与首次配对

通过 libnacl/libsodium sealed boxes（X25519、XSalsa20-Poly1305）加密 Cookie，再以电脑 Ed25519 身份签名密文及包头；板端用自己的 Ed25519 身份签发回执。包头绑定设备、随机包 ID、递增版本及创建/到期时间。Cookie 最大 32 KiB，密文包最大 256 KiB，传输有效期 24 小时。

首次经可信 USB 等通道：两端分别运行 `device_pairing.py init --role desktop` / `--role board`，使用 `--private` 指定私密目录，`--public-output` 导出公钥文件；仅交换公钥，分别执行 `pin --role ... --peer ...`，对照指纹。私钥留在各自设备，不通过 COS 交换。工具拒绝悄悄覆盖已有身份。

BoardUpdates 持久化检查版本、包 ID、摘要和认证身份，再隔离验证网站登录及配置名单，最后切换会话并记录可重发的签名回执。重复应用包返回原回执；旧版本、错误对端、坏包和过期包拒绝。不能把解密成功当成登录验证成功。

COS 为 HTTPS 通道，存放密文；板端使用 Cookie 的本地配置和会话数据库仍是明文私密数据，依赖系统文件权限。约 128 位经典安全强度不替代端点和私钥保护；不声称具有抗量子能力。自动密钥轮换和完整轮换演练尚未交付。

密码库来源和二进制哈希见 vendor/MANIFEST.json。参考：[sealed boxes](https://doc.libsodium.org/public-key_cryptography/sealed_boxes)、[Ed25519](https://doc.libsodium.org/public-key_cryptography/public-key_signatures)。
