# 当前板部署脚本记录

这些脚本从实际部署工作目录归档，用于未来换板时查阅，不是一键安装器。请先阅读 [换板恢复清单](../../docs/换板恢复清单.md)。

- prepare_root.py：下载并校验Ubuntu Base，安全解包独立rootfs。
- restore_directory_modes.py：恢复解包后的目录权限。
- download_packages.py：下载本次使用的QQ与NapCat安装包并记录校验值。
- install_app.py：解包QQ、安装NapCat入口并准备专用用户。
- prepare_boot.py：WebUI限制回环监听、生成私密Token。
- setup_lab.py：临时挂载、DNS、设备节点和cgroup v1内存上限。
- stop_test.py：按所属控制进程停止受控QQ进程组。
- install_relay.py：从板端私密incoming配置生成回环OneBot连接；不含真实凭据。
- enable_relay.py：确认校时后启用转发并登记init。

实际运行路径是 `/overlay/napcat-lab`，独立rootfs及Python路径依赖原板布局。组3003、cgroup v1、用户UID和动态库依赖需根据新系统核对。
