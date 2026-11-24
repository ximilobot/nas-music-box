# 群晖部署详解

[← 返回说明](../README.md)

按 x86 群晖（DSM）写，这是唯一实测过的环境。走的是「在 NAS 上从源码构建」，不依赖预构建镜像。

## 1. 准备文件夹

1. 用 File Station 在 `docker` 共享文件夹里新建 `nas-music-box`，把本仓库下载下来（GitHub 页面 Code → Download ZIP），把里面的 `docker-compose.yml` 和整个 `server` 文件夹上传进去。上传后应当是：
   - `/volume1/docker/nas-music-box/docker-compose.yml`
   - `/volume1/docker/nas-music-box/server/Dockerfile` 等
2. 在 `/volume1/docker/nas-music-box/` 下再新建一个空文件夹 `data`（存配置、数据库、登录凭据、转码缓存）。
3. 歌曲默认按 `/volume1/music` 读取。你的歌不在这里的话，改 `docker-compose.yml` 里 `volumes` 的第一行冒号左边的路径。

## 2. 启动

**DSM 7.2 及以上（Container Manager）**

1. 打开 Container Manager →「项目」→「新增」。
2. 项目名称填 `nas-music-box`，路径选 `/volume1/docker/nas-music-box`，来源选「使用现有的 docker-compose.yml」。
3. 一路下一步（「网页门户」那步不用设置），勾选「项目创建后立即启动」，完成。
4. 第一次会在 NAS 上构建镜像（下载 Python 基础镜像、安装 ffmpeg），需要几分钟。构建日志里卡住或报错，多半是网络问题，看下面「国内网络」。

**旧版 DSM（Docker 套件，没有「项目」功能）**

在控制面板 → 终端机和 SNMP 里启用 SSH，然后：

```bash
ssh 你的管理员账号@NAS的IP
cd /volume1/docker/nas-music-box
sudo docker-compose up -d --build      # 新版本的写法是 sudo docker compose up -d --build
```

启动后用手机或电脑浏览器打开 `http://NAS的IP:8090`。

## 3. 网络方式：host 还是端口映射

音箱是自己直接向 NAS 取音频文件的，所以程序必须知道「NAS 在局域网里的地址」，这个地址叫**音箱拉流地址**。

- **host 网络（默认，推荐）**：容器直接用 NAS 的网络，程序能自动认出 NAS 的局域网 IP，一般不用手填。代价是端口 8090 直接占用 NAS 的端口；被占用就改 `docker-compose.yml` 里的 `NASBOX_PORT`。
- **端口映射（bridge）**：把 `network_mode: host` 注释掉，启用 `ports`。这时容器里看到的是 Docker 内部 IP，自动识别不准，**必须**在网页「设置 → 音箱拉流地址」里手填 `http://NAS的局域网IP:8090`（或在 compose 里设 `NASBOX_BASE_URL`）。

两种方式下，如果 NAS 有多块网卡或开着 VPN，自动识别都可能选错网卡，也请手填。设置页会显示当前实际使用的地址。另外：用浏览器通过局域网 IP 打开网页时，程序会记下这个地址当作备选。

给 NAS 在路由器里绑定固定 IP，否则 IP 一变，手填的地址就失效了。

## 4. 群晖防火墙

控制面板 → 安全性 → 防火墙。如果启用了防火墙，新增一条规则放行 TCP 8090，来源 IP 限定为你的局域网网段（例如 192.168.1.0/255.255.255.0）。没启用防火墙就不用管。

**不要**在路由器上把 8090 映射到公网，也不要给它做外网反向代理：这个服务能控制你家音箱，并保存着小米账号的登录凭据。在外面想用，请走群晖自带的 VPN Server 或 Tailscale 之类的方式回到家里内网。

## 5. 共享文件夹权限

- 曲库是**只读挂载**（`:ro`），程序不会也不能改动、删除你的歌曲文件。
- 容器默认以 root 运行，对群晖共享文件夹开箱即用，不需要额外设置权限。
- 想用普通用户运行：SSH 里执行 `id 你的用户名` 查到 uid 和 gid（群晖第一个自建用户通常是 1026，users 组是 100），把 `docker-compose.yml` 末尾的 `user: "1026:100"` 取消注释。前提是这个用户对曲库文件夹有读取权限、对 `data` 文件夹有读写权限（File Station 右键 → 属性 → 权限）。
- `data` 文件夹里的 `credentials.json` 是小米账号的登录凭据，程序把它的权限设成仅所有者可读写。不要把 `data` 文件夹共享给别人，也不要发到网上。

## 6. 国内网络

构建镜像要从 Docker Hub 拉基础镜像、从 Debian 源装 ffmpeg、从 PyPI 装三个 Python 包，国内可能很慢或失败。`docker-compose.yml` 里留了三个参数：

```yaml
      args:
        BASE_IMAGE: python:3.12-slim       # 改成镜像站地址
        APT_MIRROR: ""                     # 例如 mirrors.ustc.edu.cn
        PIP_INDEX_URL: ""                  # 例如 https://pypi.tuna.tsinghua.edu.cn/simple
```

- `APT_MIRROR`、`PIP_INDEX_URL`：填上面注释里的例子即可（中科大、清华的公开镜像）。
- `BASE_IMAGE`：Docker Hub 的镜像站变动很频繁，没有一个能保证长期可用的地址。可以先在 Container Manager →「注册表」→「设置」里给 Docker Hub 配一个你正在用的镜像加速地址；或者把 `BASE_IMAGE` 写成 `镜像站域名/library/python:3.12-slim` 的形式；NAS 能走代理的话最省事。
- 改完后在项目里点「操作 → 构建」重新构建。

## 7. 升级与备份

- 升级：用新的 `server` 文件夹覆盖旧的，在项目里「操作 → 构建」。`data` 文件夹不要动，登录状态、歌单、收藏都在里面。
- 备份：备份 `data` 文件夹即可。
