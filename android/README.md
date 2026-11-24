# NAS音乐盒 · 安卓 App

NAS 音乐盒的手机遥控器。音乐在小爱音箱上播放，手机不出声；界面是 NAS 上服务端提供的网页（默认端口 8090），这个 App 是套在外面的原生壳。

- 包名 `com.nasmusicbox.app`，版本 1.0.0（versionCode 1）
- 支持 Android 7.0（API 24）及以上，targetSdk 34
- 只申请两个权限：联网（INTERNET）、读取网络状态（ACCESS_NETWORK_STATE）
- 纯 Java，不依赖 AndroidX 或任何第三方库，APK 约 46 KB

## 安装

把 `NAS音乐盒.apk` 传到手机上点开安装。它不是从应用商店来的，系统会要求允许「安装未知来源应用」。

这个 APK 用**自生成的密钥签名（自签名）**。以后升级必须用同一个密钥签出来的包才能覆盖安装；换了密钥只能先卸载再装，App 里保存的服务器地址会丢。

## 用法

1. 第一次打开会进设置页。填 NAS 上服务的地址，例如 `http://192.168.1.10:8090`，点「测试并保存」。App 会请求 `<地址>/api/status`，连通了才保存。
2. 可以存多个地址（比如家里内网一个、外网一个）。在设置页点一下某个地址就切换过去；每个地址都能修改、删除。
3. 主界面右侧有个半透明的「⋮」悬浮按钮，可以拖到别处。点它：刷新、切换服务器、服务器设置、屏幕常亮开关、隐藏这个按钮。
4. 长按桌面上的 App 图标 →「服务器设置」，不打开播放器也能改地址。
5. 手机音量键：网页接管时调的是音箱音量；网页没接管时就是平常的手机音量。
6. 连不上时显示中文错误页，上面有「重试」和「改地址 / 换服务器」。
7. 返回键先退网页里的上一页，退到头就把 App 放到后台。

## 网页可以调用的接口

App 往网页里注入了 `window.NasBoxApp`，网页据此判断自己是不是跑在 App 里：

| 方法 | 作用 |
| --- | --- |
| `NasBoxApp.getVersion()` | 返回 App 版本号字符串，如 `"1.0.0"` |
| `NasBoxApp.openSettings()` | 打开原生的服务器设置页 |
| `NasBoxApp.reload()` | 刷新当前页 |
| `NasBoxApp.showMenu()` | 弹出原生菜单（和悬浮按钮是同一个菜单） |
| `NasBoxApp.setBarColor(css)` | 把状态栏 / 导航栏改成指定颜色，如 `'#12131a'`；网页切换深浅色后调用 |

网页定义 `window.__nasbox_volume = function (delta) { ... }` 后，按手机音量键会收到 `+1` 或 `-1`，系统音量条不再出现。没定义这个函数时，按键照常调手机音量。

状态栏 / 导航栏颜色：页面加载完后，App 读取 `<meta name="theme-color">`，没有就取 `body` 的背景色。App 自己的原生页面（设置页、错误页）跟随系统深浅色。浏览器 UA 末尾带 `NasMusicBox/<版本>`。

## 服务端需要配合的地方

- `GET /api/status` 要免登录、返回 HTTP 200 和 JSON（以 `{` 或 `[` 开头）。返回别的内容，App 都当作「不是 NAS 音乐盒」，不保存。
- 首页的主文档如果返回 4xx / 5xx，App 会盖上原生错误页，不显示网页自己的报错内容。
- 和服务器不同源的链接一律交给系统浏览器打开。

## 自己构建

不用 Gradle，只用 JDK 和 Android build-tools 里的命令行工具：`aapt2 → javac → d8 → zipalign → apksigner`。在 Linux x86_64 上：

```bash
./setup-sdk.sh    # 只需一次：下载 JDK 17、build-tools 34.0.0、platform 34（约 1.2 GB）
./build.sh        # 构建并签名，成品在上一级目录的 NAS音乐盒.apk
./run-tests.sh    # 可选：在电脑上跑地址校验和「测试连接」的逻辑测试
```

构建环境默认放在 `~/workspace/work/xiaoai-nas/android-build/`，可用环境变量 `NASBOX_BUILD_HOME` 改到别处；成品路径可用 `NASBOX_OUT` 改。

**签名密钥**在 `<构建环境>/keystore/` 下：`nasmusicbox-release.jks`（密钥库，别名 `nasmusicbox`）和 `keystore.pass`（口令）。第一次构建时自动生成，以后每次构建都复用。这两个文件要一起备份，丢了就再也签不出能覆盖升级的包。

发新版本时改 `build.sh` 顶部的 `VERSION_CODE`（每次加 1）和 `VERSION_NAME`。

## 目录

```
AndroidManifest.xml
res/                       布局、配色（含深色）、矢量图标、桌面快捷方式
src/com/nasmusicbox/app/
  MainActivity.java        WebView 主界面、错误页、悬浮菜单、JS 桥、音量键
  SettingsActivity.java    服务器设置页
  ServerUrl.java           地址校验与规整（纯 Java）
  ConnTester.java          测试连接（纯 Java）
  Store.java               本机设置存取
  Bars.java                状态栏 / 导航栏配色
test/LogicTest.java        电脑上跑的逻辑测试
build.sh  setup-sdk.sh  run-tests.sh
```

## 已知限制

- 没有下拉刷新手势，刷新走悬浮菜单或 `NasBoxApp.reload()`。
- HTTPS 证书无效（自签名、过期）的地址会被拦下，不提供「仍然继续」。
- 地址必须当场连得通才能保存，所以外网地址要在连得上的网络下添加。
