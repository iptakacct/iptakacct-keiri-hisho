# python3を優先し、動かなければpythonにフォールバックする。
# Windows環境ではpython3が「Microsoft Storeを開くだけの偽スタブ」になっていることがあり、
# 実際にバージョン取得ができるかどうかで判定する。
# 各Bashラッパースクリプトからsourceして使う。
# freee-api-post/lib/find_python.sh・mf-api-post/lib/find_python.sh と同内容（意図的な複製）。
find_python_bin() {
  if python3 --version >/dev/null 2>&1; then
    echo "python3"
  elif python --version >/dev/null 2>&1; then
    echo "python"
  else
    echo "エラー: python3もpythonも実行できませんでした" >&2
    return 1
  fi
}
