---
name: mf-monthly-check
description: 「月次チェックして」「〇〇社の月次チェック」のように言われたら使う（マネーフォワード クラウド会計を使う会社の場合）。仕訳・PL・BSデータをもとに重複取引・定例取引の計上漏れ・勘定科目の異常な増減・BSのマイナス残高・BS科目（売掛金/未払金等）の滞留候補・法人カード請求書取り込みの二重計上・前払費用の当月償却漏れ・経路違いの二重計上・繰越のずれ・営業赤字や営業利益の大幅な減少を検出し、A/B/C重要度付きのレポートを作成する。読み取り専用（自動仕訳登録は行わない——登録はmf-fee-postの役割）。freeeの会社は freee-monthly-check を使う。
---

# 概要

マネーフォワードクラウド会計のデータをもとに、毎月の経理データを自動チェックする読み取り専用ツール。`freee-monthly-check`と同じ設計思想（「異常・漏れ・重複・残高不整合などの可能性がある項目を自動抽出し、人間が確認すべき項目を一覧化する」）だが、**データ取得はClaude自身が行い、組み立てたJSONをCLIに渡す**点が異なる。

データ取得は自前API（`mf-api-post`の`read`）を使う。会社名を指定するだけで始められ、MCPの`/mcp`接続操作は要らない。戻り値の形はMCP（`mfc_ca`）と同じなので、下記の入力JSONの組み立て方は変わらない。`mf-api-post`が未設定でMCP接続しか無い環境では、同じ項目を`mfc_ca`のMCPツール（`mfc_ca_getJournals`・`mfc_ca_getReportsTrialBalanceProfitLoss`・`mfc_ca_getReportsTrialBalanceBalanceSheet`）で取得して同じJSONにまとめればよい。

## チェック項目

1. **重複取引**：（取引日・金額・借方科目名・貸方科目名）が一致する仕訳
2. **定例取引の計上漏れ**：過去数ヶ月毎月発生している摘要パターンが当月に無い
3. **勘定科目の異常な増減**：過去平均に対する当月の増減率
4. **BS異常残高**：本来マイナスにならない資産・負債科目のマイナス残高
5. **BS科目の滞留候補**（`check_stale_subaccounts`）：売掛金・買掛金・未払金・未払費用・仮払金・仮受金・立替金・預り金・前払費用・未収入金・前受金の補助科目（無ければ取引先名、それも無ければ科目名）ごとに、直近`stale_lookback_months`（デフォルト3ヶ月）の借方・貸方の出現サイドを見て、片側にしか出現していない＝一度も消込されていない候補をB判定で報告する。金額の相殺までは見ない簡易判定なので、サイト未到来の正常なケースも拾う。**「常に片側でしか出現しない補助科目は正常な設計」と決めつけて自動的に除外しない**（片側専用に見える場合は「なぜ反対側が無いのか」＝未確定仕訳の滞留、別科目への誤記帳等を必ず掘り下げる）。`is_realized: false`（未実現・未確定）の仕訳は機械的に判定できるため除外済み（請求書機能で自動生成される仕訳案は確定しない限り未実現のまま残り、試算表にも影響しない）
6. **法人カード請求書取り込みの二重計上**（`check_upsider_billing_duplicate`）：カード利用明細のAPI連携で日々計上される未払金に対し、請求書取り込み（`entered_by: JOURNAL_TYPE_BILLING`）経由でカード会社宛の請求書が計上されるとA判定
7. **前払費用の当月償却漏れ**（`check_prepaid_expense_amortization_missing`）：「前払費用」勘定の借方合計から貸方合計を差し引いた簡易残高が正なのに、対象月に貸方（償却）エントリが一件も無い場合にB判定。**既知の限界**：科目全体の粗い判定で、対象月に何か1件でも償却があれば他の未償却案件が漏れていても検出できない。補助科目単位の追跡は改善候補

8. **経路違いの二重計上**（`check_cross_route_duplicate`）：同じ相手（補助科目、無ければ取引先）・同じ金額（行ごとの税込）・同じ借方科目の負債計上（買掛金・未払金・未払費用の貸方）が、入力の経路の違う仕訳（手入力とCSV取り込み、手入力と請求書連携等）で31日以内に2回入っていればA判定。片方が対象月に入っていることが条件。借方の科目が違えば別の取引とみなす。元の請求書が1枚なら二重計上なので、片方を画面から削除してもらう（取消仕訳で回避しない）
9. **繰越のずれ**（`check_carryover`）：前期末BSの`closing_balance`と当期首BSの`opening_balance`を科目ごとに突き合わせ、ずれていればA判定（下記「繰越のずれチェックの入力」）
10. **損益の推移**（`check_profit_trend`）：レポートに「## 損益の推移」（直近3か月の売上高・営業利益）が毎回付く。当月が営業赤字ならA、前月黒字から半分以下に減った（かつ10万円以上減った）らBで、利益への影響が大きい科目を5つ添える（閾値は`config.profit_drop_ratio`・`profit_drop_floor`）。**A・Bが出たら、報告の前に原因（売上の補助科目別・費用の科目別の前月差、見積りのままの売上、計上漏れ）まで調べてから出す**。赤字や利益が大きく減ったところは、依頼されなくても月次チェックで見る

**補助科目の付け忘れチェック**：`monthly-closing-checklist.md`項目8の注記（定例取引の照合と同じ走査で補助科目の付け忘れを拾う）を`check_missing_subaccount`関数として実装。照合キーは（科目名, 摘要を正規化したもの〈空白・数字を除去〉。摘要が無ければ取引先名）。対象月に補助科目なしで計上された行のうち、直近`subaccount_lookback_months`（デフォルト2ヶ月）の同じキーの行が1ヶ月以上あり、かつ全て補助科目付きだったものを重要度Bで検出する。過去に補助科目なしが混在するキー（運用が揺れているだけ）、摘要も取引先名も無い行、`is_realized: false`の仕訳は対象外。定例取引チェックと同じjournalsを使うので追加のデータ取得は不要。

## 使い方

1. 対象の会社名を確認する（`read`等の各コマンドは呼ぶたびに`/offices`で会社を確認するので、MCPのような接続操作は不要）
2. Claudeが`mf-api-post`の`read`コマンド（`python .claude/skills/mf-api-post/lib/mf_cli.py --companies-json .claude/skills/mf-api-post/companies.json --repo-root . read <会社> "<パス>" ...`）で以下を取得し、1つのJSONにまとめる（対象月は省略時、実行日の前月）：
   - `read <会社> "/journals?start_date=...&end_date=..." --all-pages`（対象月＋過去6ヶ月分。ページングは`--all-pages`が自動で行う）
   - `read <会社> "/reports/trial_balance_pl?start_date=...&end_date=..."`（対象月＋過去6ヶ月、各月とも月初〜月末を指定。`start_date`=`end_date`は使わない）
   - `read <会社> "/reports/trial_balance_bs?start_date=...&end_date=..."`（対象月末時点、`start_date`=会計年度開始日〈`accounting-rules.md`参照〉、`end_date`=対象月末日）
   - 取得できなかった項目があれば、該当キーを`null`にし`fetch_errors`にエラー内容を記録する
3. `read`の`--out`で保存先を指定するか、既定の保存先（`<インスタンス>/work/mf-read/`）から読み、上記を1つの入力JSONに組み立てて保存する（例：`<インスタンス>/work/mf-monthly-check/raw/<会社>-YYYY-MM-input.json`。git管理対象外）
4. CLIを実行する：
   ```bash
   python .claude/skills/mf-monthly-check/lib/mf_monthly_check_cli.py \
     --input <インスタンス>/work/mf-monthly-check/raw/<会社>-YYYY-MM-input.json \
     --output <インスタンス>/work/monthly-check/<会社>-YYYY-MM.md
   ```
5. 生成されたレポートを読み、チャットへA/B/C件数と主要項目を要約報告する

## 既知の制約

### 重複取引チェックの誤検知

重複判定のキーに摘要を含めない設計（表記ゆれで本当の二重登録を見逃さないため）のため、無関係な取引がたまたま同じ日付・金額・科目になった場合に誤検知する。典型例は同一日に多数発生する定額の振込手数料。

**人が読む際の判断基準**：金額・日付・科目が一致していても、(a) 借方・貸方いずれかの`trade_partner_name`が異なる、または (b) 仕訳メモに含まれる請求書番号（請求書管理システムが一意に振る番号）が異なる場合は、重複の可能性は低い。`read <会社> "/journals/{id}"`（MCPなら`getJournalById`）で該当仕訳を確認し、該当すれば「対応不要」と報告してよい。

### 増減チェックの過去平均が会計年度をまたぐ影響

過去6ヶ月の母数が会計年度をまたぐ場合、前期の決算月の一時的な大口仕訳が平均を歪めることがある。期首直後の月を対象にするとB判定が大量に出るわりに示唆が薄くなる。

### 複合仕訳（branches）の科目名推定

マネーフォワードは複合仕訳を借方リストと貸方リストの位置対応で返し、行数の少ない側を`null`で埋める。`null`側の科目名は同一仕訳内の他branchから補完している（`flatten_journal_branches`のdocstring参照）。ベストエフォートな推定であり、厳密な複式簿記の復元ではない。

### 仕訳の`number`は会計期間ごとにリセットされる（重要）

`number`（取引No.）は事業者全体で一意ではなく、**会計期間（`term_period`）が変わると1からリセットされる**。複数の会計期間にまたがってjournalsを収集・マージする際、**`number`をキーにして重複排除・マージしてはいけない**（異なる期間の同じ`number`同士が衝突し、一方が消える）。必ず`id`をユニークキーとして使う。

## 入力JSONのスキーマ

```json
{
  "target_month": "2026-07",
  "company_name": "株式会社サンプル",
  "journals": [ "read（/journals）の'journals'配列を対象月〜過去6ヶ月分すべて結合したもの" ],
  "trial_pl_by_month": { "2026-01": ["read（/reports/trial_balance_pl）の'rows'"], "...": "..." },
  "trial_bs": [ "read（/reports/trial_balance_bs）の'rows'（対象月末時点）" ],
  "trial_bs_prior_closing": { "科目名": [0, 0, 0, "前期末のclosing_balance", 0] },
  "trial_bs_current_opening": { "科目名": ["当期首のopening_balance", 0, 0, 0, 0] },
  "fetch_errors": { "journals": null, "trial_pl": null, "trial_bs": null },
  "config": {
    "duplicate_known_safe_pairs": [ ["支払手数料", "普通預金"] ],
    "subaccount_lookback_months": 2,
    "stale_lookback_months": 3,
    "variance_exempt_accounts": [ "交際費" ],
    "profit_drop_ratio": 0.5,
    "profit_drop_floor": 100000
  }
}
```

`config`は省略可。`duplicate_known_safe_pairs`は、オーナーが「この科目の組み合わせは重複の可能性が無い」と確認済みの（借方科目名, 貸方科目名）の組み合わせ。**会社ごとの設定は各インスタンスの`context/company/accounting-rules.md`の「重複チェックで除外してよい科目の組み合わせ」節に記録し、実行時にそこから読んで`config`に入れる**。

`variance_exempt_accounts`は、発生が不定期（特定の条件が揃った月にしか発生しない）と分かっている勘定科目名の配列。増減チェックで、**当月がゼロ発生（＝「-100%」の誤検知パターン）の場合に限り**検出対象から除外する（完全一致のみ）。当月に実際の金額が発生した場合は通常どおり検出される（急な高額発生は見逃さない）。`freee-monthly-check`の`variance_exempt_accounts`と同じ仕様。会社ごとに、実際に「発生が不定期な科目の-100%」が判明した時点で`accounting-rules.md`に記録し、実行時に`config`へ入れる。

### 繰越のずれチェックの入力

⚠️ **マネーフォワードは、会計期間を締めても繰越処理をしないと翌期の期首残高が入らない**。繰越を忘れると、翌期のBSが前期末とずれたまま1年分の記帳が進み、決算で初めて気づくことになる。これを毎月の月次チェックで拾う。

`trial_bs_prior_closing`と`trial_bs_current_opening`の**両方があるときだけ**チェックする（片方だけ・無い会社では黙って飛ばす）。作り方：

```bash
# 前期末（前期の最終月）
read <会社> "/reports/trial_balance_bs?start_date=<前期末月の1日>&end_date=<前期末日>"
# 当期首（当期の初日だけ）
read <会社> "/reports/trial_balance_bs?start_date=<当期初日>&end_date=<当期初日>"
```

どちらも`rows`を`extract_account_leaves`にかけた形（`{科目名: values}`）で入れる。会計期間は`read <会社> "/term_settings"`で分かる。**対象にするのは、前期が終わっている会社だけ**。判定は「前期末の`closing_balance`＝当期首の`opening_balance`」の単純な突合で、ずれていれば①繰越がまだ、②繰越の後に前期へ遡って仕訳を入れた、のどちらか。機械的には見分けられないので、両方を添えて要確認（A）に出す。**繰越の実行はオーナー（または担当者）がMFの画面で行う**（APIに繰越の口は無い）。

### 証憑（ファイルボックス）の未添付チェック（対話手順）

マネーフォワードは証憑（ファイルボックス）の一覧を取るAPIが無く、MCPでも自前APIでも取得できない。オーナーの依頼があれば、ログイン済みブラウザ（claude-in-chrome）でMFの「ファイルボックス」を開き、「仕訳未登録」「未添付」のフィルタで残っている証憑を一覧し、日付・金額・取引先が一致する既存仕訳（`read <会社> "/journals?..."`で検索）があれば「添付候補」としてレポートに添える。添付操作自体は行わない（画面操作はオーナーが行う）。

### known_safe_pairsの典型例（会社ごとに確認してから適用する）

- **支払手数料／普通預金**：多数の異なる振込に対する定額の銀行手数料。同日同額が多数あっても`transaction_id`が別々＝別々の実在振込
- **ツール費／未払金**：LINE公式アカウント・UTAGE・Google Workspace等、複数契約・従量課金により同日・同額・同摘要の仕訳が毎月繰り返し発生するベンダーがある場合。`read <会社> "/journals/{id}"`で`trade_partner_code`・`transaction_id`が個々に異なることを確認してから適用する。**注意**：科目名単位でしか除外できないため、この設定によりツール費全体の重複検知が効かなくなる（他ベンダーは通常月1回請求で同日重複が起きにくいため実害は小さい）。摘要単位の除外指定は改善候補

## テスト

`pytest .claude/skills/mf-monthly-check/tests/`
