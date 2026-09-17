# kessan：会計システムなしで帳簿を作る

設計：`docs/superpowers/specs/2026-09-16-kessan-without-accounting-system-design.md`（全体）、`docs/superpowers/specs/2026-09-17-kessan-stage2-reading-design.md`（段階2：資料の読み取り・確認・承認）

数字（集計・残高・検算・突き合わせ・二重防止）はすべてこのスクリプトで確定させる。AIは資料の読み取りと科目の判断だけを行い、暗算で数字を作らない。AIは `staging.csv`・`journal.csv`・`evidence.csv`・`import-log.csv`・`statement-balances.csv`・`discard-log.csv` を直接編集せず、必ずコマンドを通す。手順はスキル `.claude/skills/kessan-books/SKILL.md`。

## 準備

Python 3 と、次のライブラリを入れる。

```bash
pip install pyyaml openpyxl pytest
```

## 年度フォルダ（インスタンスの `work/kessan/<年度>/`）

| ファイル | 中身 |
|---|---|
| `period.yaml` | 事業年度の期首日・期末日（`init --start --end` で作成）。期間外の明細は取り込まず、期間外の日付は登録できない |
| `inbox/` | 受領資料の原本（gitに入れない） |
| `extracted/` | AIが資料を読んで書いた読み取り結果（`<資料のファイル名>.json`。例：`通帳-2025-04.pdf.json`）。gitで管理する |
| `staging.csv` | 取り込んだ明細・仕訳候補。`承認` 列が `済` の行が `post` で登録される |
| `journal.csv` | 帳簿本体。直接編集しない（`post` 経由で登録する）。伝票番号は数字の連番 |
| `adjustments.csv` | 決算整理仕訳（段階3で使う）。伝票番号は `A1`, `A2`… と「A」で始め、journal.csv と同じ番号を使わない（検算「伝票番号」で確認） |
| `import-log.csv` | 取り込み記録。銀行CSVはファイル名、読み取り結果は資料のパス（`inbox/…`）で記録する。`口座ID` 欄は、銀行CSV・通帳は口座ID、出納帳は `出納帳:<科目>`、領収書・請求書は種類 |
| `statement-balances.csv` | 明細（CSV・通帳・出納帳）に載っていた残高（検算で帳簿と照合） |
| `evidence.csv` | 証憑（領収書・請求書）と明細行の対応。`状態` は 明細に対応／新規仕訳／複数候補／未払候補／破棄（`discard` で証憑から作った行を破棄した）。末尾の `支払期日`・`支払方法の推定`・`自信度` は読み取り結果の値（段階3の未払管理の材料） |
| `discard-log.csv` | `discard` で破棄した行の記録（日時・取り込み元ID・日付・金額・摘要・理由） |
| `opening-balances.csv` | 期首残高（前期決算書の期末残高。残高は正常残高側をプラスで書く。減価償却累計額もプラス） |
| `output/` | `check-result.md`（検算結果）、`trial-balance.csv`（試算表）、`review-YYYYMMDD.xlsx`（確認用Excel）、`set-accounts-YYYYMMDD.json`（AIの科目候補） |

インスタンスの `context/company/` には次を置く。

| ファイル | 中身 |
|---|---|
| `kessan-sources.yaml` | 口座・カード・現金と明細CSVの形式、`receipt_default`（領収書の既定の支払方法：`立替`／`現金`／空欄） |
| `kessan-patterns.yaml` | 確立済みパターン（自動承認の根拠。オーナーの承認後に追加する） |

## 手順

インスタンスフォルダで実行する（`<Y>` は `work/kessan/2026-03期` など）。

```bash
python ../.claude/scripts/kessan/cli.py init --year-dir <Y> --start 2025-04-01 --end 2026-03-31
# opening-balances.csv に期首残高を入れる
# context/company/kessan-sources.yaml に口座と明細形式を登録する
python ../.claude/scripts/kessan/cli.py inbox-status --year-dir <Y>          # 未処理の資料の一覧
python ../.claude/scripts/kessan/cli.py import-bank --year-dir <Y> --account-id <口座ID> --file <Y>/inbox/<明細>.csv
# AIが PDF・画像・Excel を読み、<Y>/extracted/<資料のファイル名>.json を書く
python ../.claude/scripts/kessan/cli.py import-extracted --year-dir <Y>      # extracted/*.json をすべて（--file で指定も可）
# AIが要確認の行の科目候補を <Y>/output/set-accounts-YYYYMMDD.json に書く
python ../.claude/scripts/kessan/cli.py set-accounts --year-dir <Y> --file <Y>/output/set-accounts-YYYYMMDD.json
python ../.claude/scripts/kessan/cli.py review --year-dir <Y>                # 確認用Excel
python ../.claude/scripts/kessan/cli.py apply-review --year-dir <Y> --file <Y>/output/review-YYYYMMDD.xlsx
python ../.claude/scripts/kessan/cli.py approve --year-dir <Y> --review-file <Y>/output/review-YYYYMMDD.xlsx --numbers 1 2   # チャットで番号を挙げて承認されたとき
python ../.claude/scripts/kessan/cli.py approve --year-dir <Y> --ids <取り込み元ID> ...   # 行を取り込み元IDで特定して承認されたとき
python ../.claude/scripts/kessan/cli.py unimport --year-dir <Y> --source inbox/<資料>     # 読み違えた資料1件分の取り込みを取り消す
python ../.claude/scripts/kessan/cli.py discard --year-dir <Y> --ids <取り込み元ID> ... --reason <理由>   # 確認済みの重複などを破棄
python ../.claude/scripts/kessan/cli.py post --year-dir <Y>                  # 登録＋検算
python ../.claude/scripts/kessan/cli.py check --year-dir <Y> --prev-year-dir <前期のY>
python ../.claude/scripts/kessan/cli.py tb --year-dir <Y>
```

- `init` の `--start`（期首日）・`--end`（期末日）は必須。既にある年度フォルダに違う期間を指定するとエラーになる（年度フォルダの取り違え防止）。
- 明細は新しい順・古い順のどちらで出力されていてもよい（全行に残高があれば、残高の連続から判定して古い順に取り込む。残高が連続しない明細は行の欠落を疑ってエラーにする）。
- 書き込む前に staging.csv・statement-balances.csv・import-log.csv が書き込めるか確かめる。途中の書き込みで失敗したときは、どこまで書いたかを出して止まる。閉じてから同じコマンドを再実行すると、明細の行は二重に取り込まず、書けなかった残高の記録・取り込み記録を埋める。
- 期間が重なる明細を同じ口座に取り込むと警告を出す。日付・金額が取り込み済みの行と一致する行は、`要確認理由` に「二重取り込みの疑い」と入る。
- 銀行CSV・通帳・出納帳の取り込みで新しく追加する支払（出金）の行は、`evidence.csv` に金額が同じで日付が前後7日以内の証憑が無いかも確認する。「新規仕訳」の証憑があれば `要確認理由` に「証憑から計上済みの仕訳と金額・日付が近い（二重計上の疑い）」、「未払候補」の証憑があれば「未払候補の証憑と金額が一致（支払の可能性）」を追記する（既存の理由は消さず「／」で連結）。「新規仕訳」の証憑から作った行（`receipt:`）が staging にあり未承認なら、その行にも「明細にも同額の支払がある（二重計上の疑い）」を追記する。どちらもAIの判断だけで承認・削除せず、オーナーに同一取引かどうかを確認する。
- 複合仕訳は、`staging.csv` の複数行に同じ `伝票番号`（例：`T1`）を入れる（日付は全行同じにする）。登録時に正式な連番に振り直す。
- 検算でNGが1件でもあれば、決算の工程に進まない。

## 読み取り結果の取り込み（import-extracted）

- 形式の誤り（必須項目の欠落、日付・金額が読めない、期間外の日付、未登録の口座ID・科目）があるファイルは丸ごと取り込まず、ファイル名と理由を出す（終了コード4。他のファイルは取り込む）。直して再実行する。
- **1回の実行の中では、渡した読み取り結果ファイルの順番によらず、通帳・出納帳（明細）を先に、領収書・請求書（証憑）を後に自動で並べ替えて処理する**（証憑を先に処理すると、まだ明細が無く突き合わせ候補が見つからないまま新しい仕訳を余分に作ってしまうため。各グループ内は渡した順序のまま）。ただし銀行CSVの取り込み（`import-bank`）は別コマンドで、この並べ替えの対象外なので、証憑より先に済ませておく。
- **通帳**：ページごとに「繰越残高＋入金−出金＝残高」を確かめ、ページ内・前のページとの間で合わないページの行は `読み取り信頼度=低`・`要確認理由=ページの残高が連続しない` で取り込む。取り込み元IDは銀行CSVと同じ作り方なので、同じ取引をCSVと通帳の両方で受け取っても二重に入らない。
- **出納帳**：JSONの `科目`・`補助` の明細として取り込む。その（科目, 補助）が `kessan-sources.yaml` の `accounts` に登録されていなければ形式エラー（登録が無いと、出納帳の行が証憑との突き合わせ・二重計上の検知の相手にならないため。現金なら `id: cash`・`科目: 現金` を登録する）。
- **領収書・請求書**：`kessan-sources.yaml` の口座・カード・現金の支払（貸方）の行（journal.csv・staging.csv）のうち、金額が同じで日付が証憑の前後7日以内（両端を含む）、まだ証憑が付いていない行と突き合わせる。
  - 1件：新しい仕訳は作らず `evidence.csv` に対応を記録。staging の行で相手科目が空なら科目候補・取引先を入れ、`要確認理由` に「証憑と一致」を追記する（既存の理由は消さない。外すのは「相手科目未設定」だけ）。証憑の取引先名が明細の摘要に無ければ「取引先名が摘要に無い（突き合わせ先を確認）」も追記する。journal.csv は書き換えない。
  - 複数：新しい仕訳は作らず、`evidence.csv` に 複数候補 として記録（確認用Excelの「突き合わせ先が複数」）。
  - なし：支払方法で新しい仕訳の候補を作る（取り込み元ID `receipt:<証憑ID>`、`要確認理由=明細に該当なし`）。立替→貸方 役員借入金、現金→貸方 現金、口座・カード→貸方は空（明細の取り込み漏れを確認）。**領収書**で不明→`receipt_default`（未設定なら貸方は空）。後払い→仕訳を作らず 未払候補。**請求書は不明でも `receipt_default` を適用せず、後払いと同じ「未払候補」にする**（銀行振込での支払と二重計上になりやすいため。`receipt_default` は領収書のみに適用する）。
  - 同じ資料ファイルの再取り込みは `import-log.csv` で止める（件数0）。証憑ID（日付・金額・取引先を正規化したもののハッシュ）が `evidence.csv` にある証憑でも、**「別の」資料ファイルから来たものは取り込みを止めず**、証憑IDに `-2`・`-3` …を付けて別扱いで取り込み、`要確認理由` に「証憑の重複の疑い」（既存の証憑IDを付記）を追記する。日付・金額だけが同じ別の証憑があるときも、突き合わせの結果によらず同様に付記する（理由を書ける行が無い複数候補・未払候補などは、`import-extracted` の出力に一覧で出す）。どちらも自動では削除・統合しないので、同一の証憑かどうかをオーナーに確認する。「元」になるのは先に処理された方（`import-extracted` はファイル名の順で処理し、通帳・出納帳のグループが領収書・請求書のグループより先）で、後から処理された方に「証憑の重複の疑い」が付く。
  - `staging.csv`・`evidence.csv` まで書いて `import-log.csv` の書き込みだけが失敗した場合、再実行は「再開」として扱い、突き合わせをやり直さず記録を埋め直すだけにする（自分自身を「別ファイルからの重複」と誤認して余分な証憑・仕訳を作らないため）。
- 取り込んだ資料は `import-log.csv` に記録し、同じ資料の再取り込みは件数0になる。`種類=読めない` のファイルは取り込まず、確認用Excelの「読めなかった資料」に載る。

## 確立済みパターンと承認

- `import-bank`・`import-extracted`・`set-accounts` の後、`kessan-patterns.yaml` に一致した明細行（取り込み元IDが `bank:` の行。パターンの `取引先` は明細の `摘要` とだけ照合し、AIが書ける staging の `取引先` 欄では一致させない）を `判定=確立済み`・`承認=済` にする。対象は、パターンに一致し、かつその行の `要確認理由` が空か「相手科目未設定」だけの行に限る（読み取り信頼度が低い行、それ以外の要確認理由（「疑い」「連続しない」等）が付いた行、証憑から作った行（`receipt:`）、複合仕訳は対象外）。AIの科目候補とパターンの科目が違う行は承認せず、要確認理由に書く。パターンの `摘要キーワード`・`取引先` は2文字未満だと登録できない（`load_patterns` がエラーにする。誤爆防止）。
- `set-accounts`：`{取り込み元ID: {借方科目, 借方補助, 貸方科目, 貸方補助, 取引先, 判定, 要確認理由}}` のJSONを検証してから反映する。科目マスタに無い科目、staging に無い行、承認済みの行、`判定=確立済み` の指定、明細側（口座・カード・現金）の科目の変更は、1件でもあれば何も反映しない。`要確認理由` は上書きせず既存の理由に追記する（スクリプトが付けた理由は消えない。借方・貸方の科目が両方埋まったときだけ「相手科目未設定」を外す）。
- `review`：確認用Excel。シート「確認」は未承認の行が上。二重計上・二重取り込み・証憑の重複の疑い、支払の可能性、取引先名が摘要に無い行は赤（最優先）、読み取り信頼度が低い行はオレンジ、明細に該当なしの行は黄色。各行に非表示の「確認用指紋」列を持たせる（`apply-review` が内容の変化を検知するために使う）。
- `apply-review`：Excelの `承認` 列が `済` で `修正メモ` が空の行だけを承認する（Excel側で科目・金額を書き換えても反映しない）。ただし、確認用Excel出力後に `set-accounts` 等で内容（日付・借方・貸方・金額・摘要）が変わった行は、`承認=済` のままでも承認しない（「確認用指紋」列で検知し、変わった一覧を出す。`review` を出し直して確認する）。修正メモがある行は承認せず（承認済みなら承認を外して `判定=要確認` に戻す）、メモの一覧を出す。`承認` 欄に「済」以外（「OK」等）が書かれた行は承認せず件数を出す。Excelで「済」を消しても承認は外れない（外すのは修正メモ）。
- `approve --review-file <確認用Excel> --numbers N …`：チャットで番号を挙げて承認されたときに使う。確認用Excelで番号を取り込み元IDに置き換え、`apply-review` と同じ指紋の確認をする（Excel出力後に内容が変わった行は承認せず一覧に出す）。Excelに無い番号があれば何も変えない。
- `approve --ids …`：取り込み元IDを指定して承認する（指紋の確認は無い）。どちらも、借方・貸方の科目が埋まっていない行があれば何も変えない。

## 取り込みの取り消しと行の破棄

どちらも全部を検証してから書き（拒否する理由が1つでもあれば何も変えない）、承認済みの行・帳簿（journal.csv）に登録済みの行には触らない。

- `unimport --source inbox/<資料>`：資料1件分の取り込みを取り消す。staging.csv のその資料の行、statement-balances.csv・import-log.csv の記録、evidence.csv の証憑を消す（銀行CSVはファイル名でも指定できる）。明細に対応した証憑を取り消すときは、明細の行は消さず、未承認で科目・補助・取引先が証憑のままなら空に戻して「相手科目未設定」を追記し、「証憑と一致」を外す。その資料や対応する明細行が承認済み・登録済みのとき、その資料の明細行に別の証憑が付いているとき（先にその証憑を取り消す）は拒否する。取り消した後は、読み取り結果を直して取り込み直せる。
- `discard --ids … --reason …`：未承認の行を取り込み元IDで破棄し、`discard-log.csv` に記録する。証憑から作った行（`receipt:`）の証憑は `evidence.csv` の状態を「破棄」にする。承認済み・登録済みの行、証憑が付いた明細行は拒否する。オーナーが重複などを確認してから使う。

## Excel で CSV を扱うときの注意

- **`post`・`import-bank`・`import-extracted`・`set-accounts`・`apply-review`・`approve`・`unimport`・`discard` の前に、年度フォルダのCSVを Excel で開いていたら閉じる。** 開いたままだと書き込めず、エラーで止まる。確認用Excel（`review-YYYYMMDD.xlsx`）も、`review` を再実行する前に閉じる。
- 確認はできるだけ確認用Excelで行う（`staging.csv` を Excel で直接開かなくて済む）。
- Excel で `staging.csv` を保存するときは「CSV UTF-8（コンマ区切り）」を選ぶ。それ以外で保存すると文字コードが変わって読めなくなる。
- Excel で保存すると日付が `2025/4/1` 形式になることがある。`post` は `YYYY-MM-DD`（例：`2025-04-01`）以外の日付を受け付けないので、セルの表示形式を確認する。
- 金額に桁区切り（`1,000`）・円記号・全角数字が入っていても読めるが、マイナスの金額・金額が0の伝票は登録できない。

## 終了コード

| コード | 意味 |
|---|---|
| 0 | 正常終了（`check`・`post` は検算NGなし） |
| 1 | エラーで止まった（入力の不備など。何も書いていない。ただし、`post` でメッセージに「登録は完了済み」とある場合は帳簿への登録は済んでいて後始末だけが失敗している。`import-bank`・`import-extracted`・`set-accounts` で「取り込みは完了しています」「科目候補の反映は完了しています」とある場合は、反映は済んでいて確立済みパターンの自動承認だけが止まっている（直して同じコマンドを再実行すると、二重には反映せず自動承認をやり直す）。取り込み・取り消しの途中の書き込みで止まった場合は、メッセージに更新済みのファイルが出る） |
| 2 | コマンドの使い方の誤り（必須の引数が無い等。argparse が出す） |
| 3 | `post` で登録は完了したが、検算でNGがある（`check-result.md` を確認） |
| 4 | `import-extracted` で形式エラーのファイルがあった（そのファイルは取り込んでいない。他のファイルは取り込み済み。自動承認も止まったときは、そのエラーも出したうえで 4） |

`check` は検算NGがあると 1 を返す。

## テスト

リポジトリルートで `python -m pytest .claude/scripts/kessan/tests -v`
