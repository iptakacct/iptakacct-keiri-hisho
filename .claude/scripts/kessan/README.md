# kessan：会計システムなしで帳簿を作る

設計：`docs/superpowers/specs/2026-09-16-kessan-without-accounting-system-design.md`

数字（集計・残高・検算）はすべてこのスクリプトで確定させる。AIは資料の読み取りと科目の判断だけを行い、暗算で数字を作らない。

## 年度フォルダ（インスタンスの `work/kessan/<年度>/`）

| ファイル | 中身 |
|---|---|
| `period.yaml` | 事業年度の期首日・期末日（`init --start --end` で作成）。期間外の明細は取り込まず、期間外の日付は登録できない |
| `inbox/` | 受領資料の原本（gitに入れない） |
| `staging.csv` | 取り込んだ明細・仕訳候補。`承認` 列に `済` を入れた行が `post` で登録される |
| `journal.csv` | 帳簿本体。直接編集しない（`post` 経由で登録する） |
| `adjustments.csv` | 決算整理仕訳（段階3で使う） |
| `import-log.csv` | 取り込み記録 |
| `statement-balances.csv` | 明細に載っていた残高（検算で帳簿と照合） |
| `opening-balances.csv` | 期首残高（前期決算書の期末残高。残高は正常残高側をプラスで書く） |
| `output/` | `check-result.md`（検算結果）、`trial-balance.csv`（試算表） |

## 手順

インスタンスフォルダで実行する（`<Y>` は `work/kessan/2026-03期` など）。

```bash
python ../.claude/scripts/kessan/cli.py init --year-dir <Y> --start 2025-04-01 --end 2026-03-31
# opening-balances.csv に期首残高を入れる
# context/company/kessan-sources.yaml に口座と明細形式を登録する
python ../.claude/scripts/kessan/cli.py import-bank --year-dir <Y> --account-id <口座ID> --file <Y>/inbox/<明細>.csv
# staging.csv の相手科目を埋め、確認した行の「承認」列に「済」
python ../.claude/scripts/kessan/cli.py post --year-dir <Y>     # 登録＋検算
python ../.claude/scripts/kessan/cli.py check --year-dir <Y> --prev-year-dir <前期のY>
python ../.claude/scripts/kessan/cli.py tb --year-dir <Y>
```

- 複合仕訳は、`staging.csv` の複数行に同じ `伝票番号`（例：`T1`）を入れる。登録時に正式な連番に振り直す。
- 検算でNGが1件でもあれば、決算の工程に進まない。

## テスト

リポジトリルートで `python -m pytest .claude/scripts/kessan/tests -v`
