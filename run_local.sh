#!/bin/bash
# ローカルで実行する用。.env から設定を読み込んで tenki_line.py を動かす。
#   ./run_local.sh test      実際に1通送る（疎通テスト）
#   ./run_local.sh preview   送らずに内容を画面に出す
#   ./run_local.sh auto      本番と同じ判定で実行
set -eu
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "エラー: .env がありません。"
  echo "  cp .env.example .env  してから、中身に値を書いてください。"
  exit 1
fi

set -a
. ./.env
set +a

exec python3 tenki_line.py "${1:-preview}"
