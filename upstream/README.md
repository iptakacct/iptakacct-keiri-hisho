# upstream — 開発元からの更新取り込み

このリポジトリ（keiri-hisho）は、開発元が実運用しているプライベートリポジトリ（クライアント情報を含む）から派生した汎用版。開発元で日々改善されるスキル・ルール・hooksを、固有情報を除いた形でここへ「還流」させるための仕組み。

## 考え方

- 情報の流れは**上流 → keiri-hisho の一方向**。自動コピーはしない
- 上流のファイルは汎用的な手順と固有の実例が同じ文章に混在しているので、**差分を人（Claude）が読んで汎用化して移植**する。その後 `tools/leak-scan.py` で機械的に検査し、通らなければ完了にしない
- 固有語リスト（クライアント名等）はここには置かず、上流側にのみ置く。`upstream/local.json`（git管理外）でその場所を指す

## ファイル

| ファイル | 役割 |
|---|---|
| `manifest.json` | 上流パス→こちらのパスの対応表（`mappings`）、意図的に取り込まないもの（`excluded`）、最後に取り込んだ上流コミット（`last_synced_commit`） |
| `local.json`（git管理外） | `{"source_root": "<上流のローカルパス>", "terms_file": "<固有語リストの相対パス>"}` |
| `sync-log.md` | 取り込み履歴と「見送り」の記録 |
| `../tools/upstream-diff.sh` | 未同期の差分を表示（`--patch`で本文、`--count`で件数） |
| `../tools/upstream-check.sh` | 週次で未同期件数をSlackに通知（スケジューラーから実行） |
| `../.claude/skills/upstream-sync/` | `/upstream-sync` スキル（差分を読み、汎用化して移植し、検査してコミット） |

## 手順（週次）

1. `upstream-check.sh` の通知が来る（または `bash tools/upstream-diff.sh` で手動確認）
2. keiri-hisho をClaude Codeで開き `/upstream-sync`
3. Claudeが差分を読み、`manifest.json` の `note` に従って汎用化・移植 → `leak-scan` → `last_synced_commit` 更新 → コミット
4. 汎用化できない変更は `sync-log.md` に「見送り」として理由を残す

## 導入先（クライアント側）では

この仕組みは開発元のためのもの。導入先のリポジトリでは `upstream/` を削除してよい（または開発元→導入先の更新配布に同じ仕組みを流用してもよい）。
