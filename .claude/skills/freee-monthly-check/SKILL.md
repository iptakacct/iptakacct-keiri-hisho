---
name: freee-monthly-check
description: 「月次チェックして」「〇〇社の月次経理チェック」のように言われたら使う（freeeを使う会社の場合）。freee会計APIから取得したデータをもとに、重複取引・定例取引の計上漏れ・勘定科目の異常な増減・BSのマイナス残高を検出し、A/B/C重要度付きのレポートを作成する。読み取り専用（freeeのデータは変更しない）。
---

# 概要

freee会計とAPI連携し、毎月の経理データを自動チェックする読み取り専用ツール。目的は自動修正ではなく、**「異常・漏れ・重複・残高不整合などの可能性がある項目を自動抽出し、人間が確認すべき項目を一覧化する」**こと。認証基盤は`freee-api-post/lib/freee_api.py`を再利用する。

チェック項目：重複取引・定例取引の計上漏れ・勘定科目の増減・BS異常残高の4種類＋レポート生成（売掛金/買掛金の詳細な滞留日数チェック、消費税、銀行未処理、固定資産等は今後の拡張）。

## 使い方

```bash
python .claude/skills/freee-monthly-check/lib/monthly_check_cli.py \
  --companies-json .claude/skills/freee-api-post/companies.json \
  --repo-root . \
  run "<companies.jsonのname>" [--month YYYY-MM]
```

`--month`省略時は直近の完了月（実行日の前月）を対象にする。レポートは`<インスタンス>/work/monthly-check/YYYY-MM.md`に保存され、実行後にチャットへA/B/C件数と主要項目を要約報告する。

## 設定（`companies.json`の会社エントリ`monthly_check`）

| キー | デフォルト | 意味 |
|---|---|---|
| `variance_threshold` | 0.30 | 増減チェックの検出閾値（±30%） |
| `recurring_min_months` | 6 | 「定例取引」とみなすのに必要な過去の連続発生月数。dealsの取得範囲もこれに連動する |
| `variance_history_months` | 6 | 増減チェックの過去平均を取る月数 |
| `subaccount_lookback_months` | 2 | 取引先・品目の付け忘れチェック（`check_missing_partner_or_item`）で遡る月数。freeeには補助科目が無いため、`monthly-closing-checklist.md`項目8注記の「補助科目の付け忘れ」を取引先(partner_id)・品目(item_id)で判定する。対象月に属性なしの明細のうち、直近この月数の同じ（勘定科目, 正規化した摘要）の明細が1ヶ月以上あり全て属性付きだったものを重要度Bで検出。摘要の無い明細は対象外 |
| `variance_materiality_floor` | 10000 | 増減チェックの重要性の基準（円）。当月・過去平均の絶対値がどちらもこの額未満の科目は検出しない（少額・散発的な科目のノイズを抑えるため） |

## レポートを読む際の前提

- 重複判定のキーは（取引日・金額・借方科目・貸方科目）で摘要を含めない（表記ゆれで本当の二重登録を見逃さないため）。取引先が異なる、請求書番号が異なる等が確認できれば「対応不要」と判断してよい
- 過去平均の母数が会計年度をまたぐと、前期末の一時的な大口仕訳が平均を歪めることがある。期首直後の月を対象にする場合はB判定を割り引いて読む

## テスト

`pytest .claude/skills/freee-monthly-check/tests/`

## 既知の制約：`/reports/trial_pl`の`closing_balance`は期間指定に関係なく期首からの累計

`start_date`/`end_date`で1ヶ月を指定しても、`closing_balance`は**期首からその月末までの累計**で返る（期間指定は`opening_balance`・`debit_amount`・`credit_amount`の区切りにのみ効く。`fiscal_year`+`start_month`/`end_month`指定でも同じ）。当月発生額は`closing_balance - opening_balance`で求める（`_period_amount`関数。増減チェックはこの値を使う）。発覚の経緯：ある会社の定額家賃が3ヶ月連続で毎月同額ずつ増えて見えた＝累計だった。前期末月をまたぐ比較では累計がリセットされるため「-100%」が多発する。試算表の数字を目視で読むときも同じ罠がある。
