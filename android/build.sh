#!/usr/bin/env bash
# NAS音乐盒 安卓 App 构建脚本（不用 Gradle）：aapt2 → javac → d8 → zipalign → apksigner。
# 可重复执行；每次从头构建。先跑一次 ./setup-sdk.sh 准备 JDK 和 Android SDK。
#
# 可用环境变量：
#   NASBOX_BUILD_HOME  构建环境目录（JDK / SDK / 签名密钥 / 中间产物），默认 ~/workspace/work/xiaoai-nas/android-build
#   NASBOX_OUT         成品 APK 路径，默认 源码目录的上一级/NAS音乐盒.apk
set -euo pipefail

VERSION_CODE=1
VERSION_NAME=1.0.0
MIN_SDK=24
TARGET_SDK=34

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_HOME="${NASBOX_BUILD_HOME:-$HOME/workspace/work/xiaoai-nas/android-build}"
OUT_APK="${NASBOX_OUT:-$SRC/../NAS音乐盒.apk}"

export JAVA_HOME="$BUILD_HOME/jdk"
export PATH="$JAVA_HOME/bin:$PATH"
export JAVA_TOOL_OPTIONS="-Xmx512m"          # 机器内存紧张时别让 JVM 吃太多
BT="$BUILD_HOME/sdk/build-tools/34.0.0"
ANDROID_JAR="$BUILD_HOME/sdk/platforms/android-34/android.jar"
KS_DIR="$BUILD_HOME/keystore"
KS="$KS_DIR/nasmusicbox-release.jks"
KS_PASS_FILE="$KS_DIR/keystore.pass"
KS_ALIAS=nasmusicbox
W="$BUILD_HOME/out"

for f in "$JAVA_HOME/bin/javac" "$BT/aapt2" "$BT/d8" "$BT/zipalign" "$BT/apksigner" "$ANDROID_JAR"; do
  [ -e "$f" ] || { echo "缺少 $f —— 先运行 ./setup-sdk.sh" >&2; exit 1; }
done

# 签名密钥：没有就生成一个自签名的。升级安装必须用同一个密钥，务必备份 $KS_DIR。
if [ ! -f "$KS" ]; then
  echo ">> 生成自签名密钥 $KS"
  mkdir -p "$KS_DIR" && chmod 700 "$KS_DIR"
  ( umask 077; head -c 24 /dev/urandom | base64 | tr -d '/+=\n' > "$KS_PASS_FILE" )
  keytool -genkeypair -keystore "$KS" -storetype PKCS12 -alias "$KS_ALIAS" \
    -keyalg RSA -keysize 2048 -validity 10950 \
    -storepass:file "$KS_PASS_FILE" -keypass:file "$KS_PASS_FILE" \
    -dname "CN=NAS Music Box, OU=Self-signed, O=nasmusicbox" >/dev/null 2>&1
  chmod 600 "$KS" "$KS_PASS_FILE"
fi

rm -rf "$W" && mkdir -p "$W/gen" "$W/classes" "$W/dex"

echo ">> 1/6 编译资源 (aapt2 compile)"
"$BT/aapt2" compile --dir "$SRC/res" -o "$W/res.zip"

echo ">> 2/6 链接资源 (aapt2 link)"
"$BT/aapt2" link -o "$W/base.apk" -I "$ANDROID_JAR" \
  --manifest "$SRC/AndroidManifest.xml" --java "$W/gen" \
  --min-sdk-version $MIN_SDK --target-sdk-version $TARGET_SDK \
  --version-code $VERSION_CODE --version-name $VERSION_NAME \
  "$W/res.zip"

echo ">> 3/6 编译 Java (javac)"
find "$SRC/src" "$W/gen" -name '*.java' > "$W/sources.txt"
javac -source 8 -target 8 -encoding UTF-8 -Xlint:-options -nowarn \
  -bootclasspath "$ANDROID_JAR" -classpath "$BT/core-lambda-stubs.jar" -d "$W/classes" @"$W/sources.txt" 2>&1 | grep -v 'JAVA_TOOL_OPTIONS' || true
[ -f "$W/classes/com/nasmusicbox/app/MainActivity.class" ] || { echo "javac 失败" >&2; exit 1; }

echo ">> 4/6 转 dex (d8)"
find "$W/classes" -name '*.class' > "$W/classes.txt"
"$BT/d8" --release --min-api $MIN_SDK --lib "$ANDROID_JAR" --output "$W/dex" @"$W/classes.txt" 2>&1 | grep -v 'JAVA_TOOL_OPTIONS' || true
[ -f "$W/dex/classes.dex" ] || { echo "d8 失败" >&2; exit 1; }
( cd "$W/dex" && zip -q -j "$W/base.apk" classes.dex )

echo ">> 5/6 对齐 (zipalign)"
"$BT/zipalign" -f -p 4 "$W/base.apk" "$W/aligned.apk"

echo ">> 6/6 签名 (apksigner, v2+v3；另附 v1 JAR 签名)"
"$BT/apksigner" sign --ks "$KS" --ks-key-alias "$KS_ALIAS" \
  --ks-pass "file:$KS_PASS_FILE" \
  --v1-signing-enabled true --v2-signing-enabled true --v3-signing-enabled true \
  --out "$W/signed.apk" "$W/aligned.apk" 2>&1 | grep -v 'JAVA_TOOL_OPTIONS' || true
[ -f "$W/signed.apk" ] || { echo "签名失败" >&2; exit 1; }
"$BT/zipalign" -c -p 4 "$W/signed.apk" || { echo "zipalign 校验失败" >&2; exit 1; }
"$BT/apksigner" verify --min-sdk-version $MIN_SDK "$W/signed.apk" >/dev/null 2>&1 || { echo "签名校验失败" >&2; exit 1; }

mkdir -p "$(dirname "$OUT_APK")"
cp -f "$W/signed.apk" "$OUT_APK"
chmod 644 "$OUT_APK"
echo "完成：$OUT_APK ($(stat -c %s "$OUT_APK") 字节)"
