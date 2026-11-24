#!/bin/sh
# 用 ffmpeg 生成带标签的测试音频。用法：make_audio.sh <输出目录>
set -e
OUT="${1:-./testmusic}"
mkdir -p "$OUT/周杰伦/叶惠美" "$OUT/陈奕迅" "$OUT/轻音乐"
FF="ffmpeg -v error -y"
$FF -f lavfi -i "color=c=red:s=64x64" -frames:v 1 "$OUT/周杰伦/叶惠美/cover.png"
gen() { # 频率 时长 输出 标题 歌手 专辑 音轨 [额外参数]
  f=$1; d=$2; o=$3; t=$4; a=$5; al=$6; n=$7; shift 7
  $FF -f lavfi -i "sine=frequency=$f:duration=$d" -metadata title="$t" -metadata artist="$a" -metadata album="$al" -metadata track="$n" "$@" "$o"
}
gen 440 2 "$OUT/周杰伦/叶惠美/01 晴天.mp3" "晴天" "周杰伦" "叶惠美" 1 -b:a 128k
gen 523 2 "$OUT/周杰伦/叶惠美/02 东风破.flac" "东风破" "周杰伦" "叶惠美" 2
gen 659 2 "$OUT/周杰伦/叶惠美/03 以父之名.m4a" "以父之名" "周杰伦" "叶惠美" 3
gen 330 2 "$OUT/陈奕迅/十年.ogg" "十年" "陈奕迅" "黑白灰" 1
gen 392 2 "$OUT/陈奕迅/浮夸.wma" "浮夸" "陈奕迅" "U87" 2
gen 294 12 "$OUT/轻音乐/长曲.mp3" "长曲" "测试乐团" "测试专辑" 1 -b:a 64k
# 无标签文件：标题应退回到文件名
$FF -f lavfi -i "sine=frequency=262:duration=2" "$OUT/轻音乐/无标签的歌.wav"
# 带内嵌封面的 mp3
$FF -f lavfi -i "sine=frequency=349:duration=2" -i "$OUT/周杰伦/叶惠美/cover.png" -map 0:a -map 1:v -c:v png -disposition:v attached_pic -id3v2_version 3 -metadata title="带封面" -metadata artist="测试乐团" "$OUT/轻音乐/带封面.mp3"
echo "generated in $OUT"
