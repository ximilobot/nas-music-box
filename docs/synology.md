# 群晖部署

[← 返回说明](../README.md)

在 x86 群晖（DSM 7.2 及以上）的 Container Manager 里直接拉取镜像部署，不需要在 NAS 上构建。

## 1. 准备文件夹

用 File Station 在 `docker` 共享文件夹里新建 `nas-music-box`，再在里面新建一个空文件夹 `data`：

```
/volume1/docker/nas-music-box/
└── data/          配置、数据库、登录凭据、转码缓存
```

## 2. 新建项目

1. 打开 Container Manager →「项目」→「新增」。
2. 项目名称填 `nas-music-box`，路径选 `/volume1/docker/nas-music-box`。
3. 来源选「创建 docker-compose.yml」，把下面的内容贴进去。歌不在 `/volume1/music` 的话，改 `volumes` 第一行冒号左边的路径。

```yaml
services:
  nas-music-box:
    image: ghcr.io/ximilobot/nas-music-box:latest
    container_name: nas-music-box
    restart: unless-stopped
    network_mode: host
    environment:
      - TZ=Asia/Shanghai
      - NASBOX_PORT=8090
    volumes:
      - /volume1/music:/music:ro
      - /volume1/docker/nas-music-box/data:/data
```

4. 一路下一步（「网页门户」不用设置），勾选「项目创建后立即启动」，完成。
5. 等镜像拉取完、容器显示运行中，浏览器打开 `http://NAS的IP:8090`。

镜像拉不下来：国内访问 `ghcr.io` 经常很慢或超时，多试几次，或者让 NAS 走代理。

## 3. 网络

音箱是自己直接向 NAS 取音频的，所以程序必须知道 NAS 在局域网里的地址（设置页里叫「音箱拉流地址」）。

- 上面用的是 **host 网络**，程序能自动认出 NAS 的局域网 IP，一般不用手填。8090 端口被占用就改 `NASBOX_PORT`。
- NAS 有多块网卡或开着 VPN 时可能认错，在网页「设置 → 音箱拉流地址」里手填 `http://NAS的局域网IP:8090`。
- 给 NAS 在路由器里绑定固定 IP，否则 IP 一变手填的地址就失效了。

## 4. 防火墙

控制面板 → 安全性 → 防火墙。启用了防火墙的话，新增一条规则放行 TCP 8090，来源 IP 限定为你的局域网网段。没启用就不用管。

**不要**把 8090 映射到公网，也不要做外网反向代理。在外面想用，请走群晖的 VPN Server 或 Tailscale 回到家里内网。

## 5. 权限

- 曲库是只读挂载（`:ro`），程序不会也不能改动你的歌曲文件。
- 容器默认以 root 运行，对群晖共享文件夹开箱即用。
- `data` 文件夹里的 `credentials.json` 是小米账号的登录凭据，不要把这个文件夹共享给别人或发到网上。

## 6. 升级与备份

- 升级：Container Manager →「映像」里选中这个镜像点「更新」（或删掉旧镜像），再到项目里「操作 → 构建」重新创建容器。`data` 文件夹不要动。
- 备份：备份 `data` 文件夹即可。
