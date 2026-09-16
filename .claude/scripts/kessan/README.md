# kessan：会計システムなしで帳簿を作る

設計：`docs/superpowers/specs/2026-09-16-kessan-without-accounting-system-design.md`

数字（集計・残高・検算）はすべてこのスクリプトで確定させる。AIは資料の読み取りと科目の判断だけを行い、暗算で数字を作らない。

## 準備

Python 3 と、次のライブラリを入れる。

```bash
pip install pyyaml pytest
```

## 年度フォルダ（インスタンスの `work/kessan/<年度>/`）

| ファイル | 中身 |
|---|---|
| `period.yaml` | 事業年度の期首日・期末日（`init --start --end` で作成）。期間外の明細は取り込まず、期間外の日付は登録できない |
| `inbox/` | 受領資料の原本（gitに入れない） |
| `staging.csv` | 取り込んだ明細・仕訳候補。`承認` 列に `済` を入れた行が `post` で登録される |
| `journal.csv` | 帳簿本体。直接編集しない（`post` 経由で登録する）。伝票番号は数字の連番 |
| `adjustments.csv` | 決算整理仕訳（段階3で使う）。伝票番号は `A1`, `A2`… と「A」で始め、journal.csv と同じ番号を使わない（検算「伝票番号」で確認） |
| `import-log.csv` | 取り込み記録 |
| `statement-balances.csv` | 明細に載っていた残高（検算で帳簿と照合） |
| `opening-balances.csv` | 期首残高（前期決算書の期末残高。残高は正常残高側をプラスで書く。減価償却累計額もプラス） |
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

- `init` の `--start`（期首日）・`--end`（期末日）は必須。既にある年度フォルダに違う期間を指定するとエラーになる（年度フォルダの取り違え防止）。
- 明細は新しい順・古い順のどちらで出力されていてもよい（全行に残高があれば、残高の連続から判定して古い順に取り込む。残高が連続しない明細は行の欠落を疑ってエラーにする）。
- 期間が重なる明細を同じ口座に取り込むと警告を出す。日付・金額が取り込み済みの行と一致する行は、`要確認理由` に「二重取り込みの疑い」と入る。
- 複合仕訳は、`staging.csv` の複数行に同じ `伝票番号`（例：`T1`）を入れる（日付は全行同じにする）。登録時に正式な連番に振り直す。
- 検算でNGが1件でもあれば、決算の工程に進まない。

## Excel で CSV を扱うときの注意

- **`post` の前に、`staging.csv`・`journal.csv`・`import-log.csv` を Excel で開いていたら閉じる。** 開いたままだと書き込めず、何も登録せずにエラーで止まる。
- Excel で `staging.csv` を保存するときは「CSV UTF-8（コンマ区切り）」を選ぶ。それ以外で保存すると文字コードが変わって読めなくなる。
- Excel で保存すると日付が `2025/4/1` 形式になることがある。`post` は `YYYY-MM-DD`（例：`2025-04-01`）以外の日付を受け付けないので、セルの表示形式を確認する。
- 金額に桁区切り（`1,000`）や円記号が入っていても読めるが、マイナスの金額・金額が0の伝票は登録できない。

## 終了コード

| コード | 意味 |
|---|---|
| 0 | 正常終了（`check`・`post` は検算NGなし） |
| 1 | エラーで止まった（入力の不備など。`post` の場合は何も登録していない。ただしメッセージに「登録は完了済み」とある場合は、帳簿への登録は済んでいて後始末だけが失敗している） |
| 2 | `post` で登録は完了したが、検算でNGがある（`check-result.md` を確認） |

`check` は検算NGがあると 1 を返す。

## テスト

リポジトリルートで `python -m pytest .claude/scripts/kessan/tests -v`
