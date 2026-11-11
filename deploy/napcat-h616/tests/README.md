# 测试

这些测试主要面向开发板Linux，部分模块依赖fcntl及/proc，不适合直接在Windows执行全套。

保留当前目录结构时，在项目根目录执行：

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py'
```

各测试文件自动定位board/relay与board/maintenance。部署到开发板时也可将测试文件及对应源码放到同一测试目录，再使用开发板Python运行。测试凭据均为合成数据，不应放入真实密码或Token。
