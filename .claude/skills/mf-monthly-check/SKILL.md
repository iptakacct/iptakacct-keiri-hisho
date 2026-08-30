---
name: mf-monthly-check
description: 「月次チェックして」「〇〇社の月次チェック」のように言われたら使う（マネーフォワード クラウド会計を使う会社の場合、mfc_ca MCP接続中に）。仕訳・PL・BSデータをもとに重複取引・定例取引の計上漏れ・勘定科目の異常な増減・BSのマイナス残高・BS科目（売掛金/未払金等）の滞留候補・法人カード請求書取り込みの二重計上・前払費用の当月償却漏れを検出し、A/B/C重要度付きのレポートを作成する。読み取り専用（自動仕訳登録は行わない——登録はmf-fee-postの役割）。freeeの会社は freee-monthly-check を使う。
---

# 概要

マネーフォワードクラウド会計とMCP連携し、毎月の経理データを自動チェックする読み取り専用ツール。`freee-monthly-check`と同じ設計思想（「異常・漏れ・重複・残高不整合などの可能性がある項目を自動抽出し、人間が確認すべき項目を一覧化する」）だが、`mfc_ca`はMCPツールとしてしか呼べないため、**データ取得はClaude自身が`/mcp`接続中に行う**点が異なる。

## チェック項目

1. **重複取引**：（取引日・金額・借方科目名・貸方科目名）が一致する仕訳
2. **定例取引の計上漏れ**：過去数ヶ月毎月発生している摘要パターンが当月に無い
3. **勘定科目の異常な増減**：過去平均に対する当月の増減率
4. **BS異常残高**：本来マイナスにならない資産・負債科目のマイナス残高
5. **BS科目の滞留候補**（`check_stale_subaccounts`）：売掛金・買掛金・未払金・未払費用・仮払金・仮受金・立替金・預り金・前払費用・未収入金・前受金の補助科目（無ければ取引先名、それも無ければ科目名）ごとに、直近`stale_lookback_months`（デフォルト3ヶ月）の借方・貸方の出現サイドを見て、片側にしか出現していない＝一度も消込されていない候補をB判定で報告する。金額の相殺までは見ない簡易判定なので、サイト未到来の正常なケースも拾う。**「常に片側でしか出現しない補助科目は正常な設計」と決めつけて自動的に除外しない**（片側専用に見える場合は「なぜ反対側が無いのか」＝未確定仕訳の滞留、別科目への誤記帳等を必ず掘り下げる）。`is_realized: false`（未実現・未確定）の仕訳は機械的に判定できるため除外済み（請求書機能で自動生成される仕訳案は確定しない限り未実現のまま残り、試算表にも影響しない）
6. **法人カード請求書取り込みの二重計上**（`check_upsider_billing_duplicate`）：カード利用明細のAPI連携で日々計上される未払金に対し、請求書取り込み（`entered_by: JOURNAL_TYPE_BILLING`）経由でカード会社宛の請求書が計上されるとA判定
7. **前払費用の当月償却漏れ**（`check_prepaid_expense_amortization_missing`）：「前払費用」勘定の借方合計から貸方合計を差し引いた簡易残高が正なのに、対象月に貸方（償却）エントリが一件も無い場合にB判定。**既知の限界**：科目全体の粗い判定で、対象月に何か1件でも償却があれば他の未償却案件が漏れていても検出できない。補助科目単位の追跡は改善候補

**補助科目の付け忘れチェック**：`monthly-closing-checklist.md`項目8の注記（定例取引の照合と同じ走査で補助科目の付け忘れを拾う）を`check_missing_subaccount`関数として実装。照合キーは（科目名, 摘要を正規化したもの〈空白・数字を除去〉。摘要が無ければ取引先名）。対象月に補助科目なしで計上された行のうち、直近`subaccount_lookback_months`（デフォルト2ヶ月）の同じキーの行が1ヶ月以上あり、かつ全て補助科目付きだったものを重要度Bで検出する。過去に補助科目なしが混在するキー（運用が揺れているだけ）、摘要も取引先名も無い行、`is_realized: false`の仕訳は対象外。定例取引チェックと同じjournalsを使うので追加のデータ取得は不要。

## 使い方

1. オーナーが`/mcp`で対象会社に接続する（`mfc_ca_currentOffice`で確認）
2. Claudeが以下のMCPツールを呼び、1つのJSONにまとめる（対象月は省略時、実行日の前月）：
   - `mfc_ca_getJournals`（対象月＋過去6ヶ月分、`start_date`/`end_date`指定、ページングあり）
   - `mfc_ca_getReportsTrialBalanceProfitLoss`（対象月＋過去6ヶ月、各月とも月初〜月末を指定）
   - `mfc_ca_getReportsTrialBalanceBalanceSheet`（対象月末時点、`start_date`=会計年度開始日〈`accounting-rules.md`参照〉、`end_date`=対象月末日）
   - 取得できなかった項目があれば、該当キーを`null`にし`fetch_errors`にエラー内容を記録する
3. 組み立てたJSONを一時ファイルに保存する（例：`<インスタンス>/work/mf-monthly-check/raw/<会社>-YYYY-MM-input.json`。git管理対象外）
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

**人が読む際の判断基準**：金額・日付・科目が一致していても、(a) 借方・貸方いずれかの`trade_partner_name`が異なる、または (b) 仕訳メモに含まれる請求書番号（請求書管理システムが一意に振る番号）が異なる場合は、重複の可能性は低い。`getJournalById`で該当仕訳を確認し、該当すれば「対応不要」と報告してよい。

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
  "journals": [ "mfc_ca_getJournalsの'journals'配列を対象月〜過去6ヶ月分すべて結合したもの" ],
  "trial_pl_by_month": { "2026-01": ["mfc_ca_getReportsTrialBalanceProfitLossの'rows'"], "...": "..." },
  "trial_bs": [ "mfc_ca_getReportsTrialBalanceBalanceSheetの'rows'（対象月末時点）" ],
  "fetch_errors": { "journals": null, "trial_pl": null, "trial_bs": null },
  "config": {
    "duplicate_known_safe_pairs": [ ["支払手数料", "普通預金"] ],
    "subaccount_lookback_months": 2,
    "stale_lookback_months": 3
  }
}
```

`config`は省略可。`duplicate_known_safe_pairs`は、オーナーが「この科目の組み合わせは重複の可能性が無い」と確認済みの（借方科目名, 貸方科目名）の組み合わせ。**会社ごとの設定は各インスタンスの`context/company/accounting-rules.md`の「重複チェックで除外してよい科目の組み合わせ」節に記録し、実行時にそこから読んで`config`に入れる**。

### known_safe_pairsの典型例（会社ごとに確認してから適用する）

- **支払手数料／普通預金**：多数の異なる振込に対する定額の銀行手数料。同日同額が多数あっても`transaction_id`が別々＝別々の実在振込
- **ツール費／未払金**：LINE公式アカウント・UTAGE・Google Workspace等、複数契約・従量課金により同日・同額・同摘要の仕訳が毎月繰り返し発生するベンダーがある場合。`getJournalById`で`trade_partner_code`・`transaction_id`が個々に異なることを確認してから適用する。**注意**：科目名単位でしか除外できないため、この設定によりツール費全体の重複検知が効かなくなる（他ベンダーは通常月1回請求で同日重複が起きにくいため実害は小さい）。摘要単位の除外指定は改善候補

## テスト

`pytest .claude/skills/mf-monthly-check/tests/`
