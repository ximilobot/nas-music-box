#!/usr/bin/env bash
# 一次性准备构建环境：JDK 17 + Android build-tools 34 + platform 34（约 1.2 GB，装在 NASBOX_BUILD_HOME 下）。
# 已装好的部分会跳过，可重复执行。
set -euo pipefail
BUILD_HOME="${NASBOX_BUILD_HOME:-$HOME/workspace/work/xiaoai-nas/android-build}"
mkdir -p "$BUILD_HOME/dl" "$BUILD_HOME/sdk/build-tools" "$BUILD_HOME/sdk/platforms"
cd "$BUILD_HOME"

if [ ! -x jdk/bin/javac ]; then
  echo ">> 下载 JDK 17 (Temurin)"
  curl -fL -o dl/jdk.tar.gz "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse"
  mkdir -p jdk && tar xzf dl/jdk.tar.gz -C jdk --strip-components=1
fi
if [ ! -x sdk/build-tools/34.0.0/aapt2 ]; then
  echo ">> 下载 Android build-tools 34.0.0"
  curl -fL -o dl/bt.zip https://dl.google.com/android/repository/build-tools_r34-linux.zip
  rm -rf dl/bt && unzip -q dl/bt.zip -d dl/bt && mv dl/bt/* sdk/build-tools/34.0.0 && rm -rf dl/bt
fi
if [ ! -f sdk/platforms/android-34/android.jar ]; then
  echo ">> 下载 Android platform 34"
  curl -fL -o dl/plat.zip https://dl.google.com/android/repository/platform-34-ext7_r03.zip
  rm -rf dl/pl && unzip -q dl/plat.zip -d dl/pl && mv dl/pl/* sdk/platforms/android-34 && rm -rf dl/pl
fi
echo "构建环境就绪：$BUILD_HOME"
