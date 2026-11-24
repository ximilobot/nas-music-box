# NAS 音乐盒

让小爱音箱播放 NAS 上的本地歌曲。服务端跑在 Docker 里，用手机网页或安卓 App 点歌，也可以直接对音箱说话点歌。

> [!IMPORTANT]
> - **自用项目，代码全部由 AI 编写**，作者只提需求、在自己家里实测，没有人工逐行审过代码。
> - **只在 Xiaomi 智能音箱 Pro（型号 OH2P）上实测过**，其他型号没有设备，无法测试也无法适配。
> - 播放指令经小米云端的非公开接口下发，小米改接口就会失效；登录小米账号的风险自行承担。
> - 不保证维护和回复 issue，可以自己拿去改（MIT）。

## 功能

- **本地曲库**：扫描音乐文件夹，读标签和封面。不联网搜歌、不下载、不上报统计，曲库只读挂载。
- **网页控制**：曲库 / 文件夹 / 歌单 / 收藏 / 搜索 / 队列 / 播放模式 / 音量 / 定时关闭。
- **语音点歌**：「播放周杰伦的歌」「我想听晴天」，曲库里有就由 NAS 播，没有才交给小爱。
- **扫码登录**：米家 App 扫码，不保存账号密码；网络抖动不会清掉登录状态。
- **格式**：mp3 / flac / m4a / wav 直接播，ape / wma / ogg 等自动转码并缓存。
- **音箱关机能识别**：停止重试、保留队列，恢复后不会自己出声。
- **安卓 App（可选）**：网页外面的原生壳，手机音量键直接调音箱音量。

## 截图

<!-- 截图占位：把图片传到 docs/images/ 后，删掉这一行和下面一行的注释符号即可显示
| 曲库 | 播放页 | 设置 |
| :---: | :---: | :---: |
| <img src="docs/images/01-library.png" width="240"> | <img src="docs/images/02-player.png" width="240"> | <img src="docs/images/04-settings.png" width="240"> |
-->

> 📷 截图待补：`01-library.png` 曲库页、`02-player.png` 播放页、`04-settings.png` 设置页

## 部署

需要一台和音箱在同一局域网、能上网的 x86 Linux 主机或 NAS，装好 Docker。

先准备两个文件夹：

| 文件夹 | 用途 |
| --- | --- |
| 音乐目录 | 你的歌，只读挂载到容器的 `/music` |
| 数据目录 | 配置、数据库、登录凭据、转码缓存，挂载到 `/data` |

### 方式一：Docker Compose（推荐）

新建 `docker-compose.yml`，把两个路径换成你自己的：

```yaml
services:
  nas-music-box:
    image: ghcr.io/ximilobot/nas-music-box:latest
    container_name: nas-music-box
    restart: unless-stopped
    network_mode: host          # 推荐，自动识别 NAS 的局域网 IP
    environment:
      - TZ=Asia/Shanghai
      - NASBOX_PORT=8090        # 端口被占用就改这里
    volumes:
      - /你的音乐目录:/music:ro
      - /你的数据目录:/data
```

```bash
docker compose up -d
```

### 方式二：docker run

```bash
docker run -d --name nas-music-box --restart unless-stopped \
  --network host \
  -e TZ=Asia/Shanghai \
  -v /你的音乐目录:/music:ro \
  -v /你的数据目录:/data \
  ghcr.io/ximilobot/nas-music-box:latest
```

### 方式三：从源码构建

镜像拉不动（国内访问 `ghcr.io` 经常很慢）时用这个。

```bash
git clone https://github.com/ximilobot/nas-music-box.git
cd nas-music-box
# 按需修改 docker-compose.yml 里的两个路径
docker compose up -d --build
```

启动后浏览器打开 `http://NAS的IP:8090`。

<details>
<summary>不用 host 网络（端口映射）</summary>

把 `network_mode: host` 换成端口映射，并**必须**告诉程序 NAS 的局域网地址，否则音箱取不到音频：

```yaml
    ports:
      - "8090:8090"
    environment:
      - TZ=Asia/Shanghai
      - NASBOX_BASE_URL=http://192.168.1.10:8090   # 换成你 NAS 的局域网 IP
```

</details>

<details>
<summary>环境变量</summary>

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `NASBOX_PORT` | `8090` | 监听端口 |
| `NASBOX_BASE_URL` | 空（自动识别） | 音箱取音频用的地址，如 `http://192.168.1.10:8090` |
| `TZ` | `Asia/Shanghai` | 时区 |

</details>

**群晖用户**：图文步骤、防火墙、权限、国内网络换源见 [群晖部署详解](docs/synology.md)。

<!-- 截图占位：<img src="docs/images/05-synology.png" width="720"> -->

> 📷 截图待补：`05-synology.png` 群晖 Container Manager 项目运行中

> [!WARNING]
> 不要把 8090 端口映射到公网。这个服务能控制你家音箱，并保存着小米账号的登录凭据。

## 使用

1. 打开 `http://NAS的IP:8090`，第一次会进入设置页。
2. 点「扫码登录」，用**米家 App** 扫码确认。

   <!-- 截图占位：<img src="docs/images/03-qr-login.png" width="240"> -->

   > 📷 截图待补：`03-qr-login.png` 扫码登录（二维码请打码）

3. 在「音箱」里选中要用的那台。
4. 到「曲库」确认歌曲已扫描出来，点一首试播。加了新歌要点「重新扫描」。
5. 点了没声音：先看设置页的「音箱拉流地址」是不是 NAS 的局域网地址。

### 语音

对音箱说「小爱同学」之后：

| 说法 | 结果 |
| --- | --- |
| 播放周杰伦的歌 | 曲库里这位歌手的全部歌曲 |
| 我想听晴天 / 来一首东风破 | 这首歌，后面接同文件夹的其他歌 |
| 播放 + 专辑名 / 歌单名 / 文件夹名 | 对应的专辑、歌单、文件夹 |
| 上一首 / 下一首 / 暂停 / 继续 / 停止 | 只在 NAS 的歌播放时生效 |
| 单曲循环 / 全部循环 / 随机播放 | 切换播放模式 |
| 加入收藏 / 取消收藏 | 针对正在播的歌 |

- 名字要和曲库里**完全一致**才由 NAS 播，否则交给小爱自己播。说「播放歌曲 + 名字」可以模糊匹配。
- 小爱会先答半句再被打断，这是读取对话记录的延迟，避免不了。
- 完整说明见 [语音点歌和口令](docs/voice.md)。

### 安卓 App

源码在 [`android/`](android/)，仓库里不放 APK；Releases 页面有就从那里下载，没有就按 [`android/README.md`](android/README.md) 自己构建。装好后填 `http://NAS的IP:8090` 即可。

### 升级

```bash
docker compose pull && docker compose up -d      # 用镜像
git pull && docker compose up -d --build         # 源码构建
```

数据目录不要动，登录状态、歌单、收藏都在里面。

## 已知局限

**型号**：只支持、只测试过 Xiaomi 智能音箱 Pro（OH2P）。

**这台音箱本身的限制**

- 没有 DLNA，不刷机只能经小米云端控制，NAS 断网或小米云端出问题时无法控制音箱。
- 不上报播放进度和当前曲目，网页上的进度条是程序按时间自己算的。
- 不支持断点续播：暂停后继续、被对话打断后恢复，都是**从头重放当前这首**。
- 切歌时可能听到约 1 秒的上一首开头。
- 分辨不了音箱在播 NAS 的歌还是小爱自己的内容，只能靠你说的那句话判断，偶尔会判断错。

**不做的**：联网搜歌、下载、歌词、多账号、账号密码登录、自动监视新歌、拼音容错。

**依赖**：小米云端的非公开接口，接口变了就会失效。

## 更多文档

- [群晖部署详解](docs/synology.md)
- [语音点歌和口令](docs/voice.md)
- [配置、排障与实现说明](docs/reference.md)（配置项、排障表、稳定性做法、实测情况、开发与测试）

## 参考

实现方式参考了开源项目 [xiaomusic](https://github.com/hanxi/xiaomusic)，代码是重新写的。

## 许可

[MIT](LICENSE)
