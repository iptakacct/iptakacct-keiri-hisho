# 税務プロファイル（決算・申告の基本情報メモ）

この会社の税務の基本情報を1か所にまとめる。`/setup` では骨組みだけ埋め、初回決算時に不足項目を秘書が質問して埋める。値は下のjsonフェンスに書く。

**書類で確かめた値だけを書く。** 推測・社長の自己申告は `unverified` にして、現物（登記簿・届出の控え・承認通知書）を確認したら値を入れて `status` を `confirmed` にする。設立書類から転記する項目は `設立書類の確認メモ.md` に対応表がある。

```json
{
  "company_name": "",
  "entity_type": "corporation",
  "fiscal_year_start": "YYYY-MM-DD",
  "fiscal_year_end": "YYYY-MM-DD",
  "established_on": null,
  "engaged_on": null,
  "capital": null,
  "capital_etc": null,
  "prefecture": "",
  "municipality": "",
  "employees": null,
  "withholding_agent": null,
  "blue_return": true,
  "consumption_tax_method": "standard",
  "filing_extension": false,
  "is_family_company": true,
  "invoice_registered": false,
  "shareholders_meeting_month": null,
  "pay_day": null,
  "officers": [{"name": "", "monthly_pay": 0, "changed_on": null}],
  "prior": {"fiscal_year": null, "corp_tax": 0, "consumption_national": 0,
            "taxable_sales": {}},
  "filings": {
    "blue_return": {"status": "unverified", "filed_on": null, "evidence": "",
                    "note": "承認通知書の現物を未確認"},
    "extension_corp_national": {"status": "not_filed"},
    "extension_corp_prefecture": {"status": "not_filed"},
    "extension_corp_municipality": {"status": "not_filed"},
    "withholding_special_period": {"status": "unverified", "note": "届出の有無を未確認"},
    "resident_tax_special_period": {"status": "not_filed"},
    "establishment_national": {"status": "unverified", "note": "設立届の控えを未確認"},
    "payroll_office": {"status": "unverified", "note": "給与支払事務所の開設届を未確認"}
  },
  "events": []
}
```

## 埋めるべき項目

| 項目 | 何を見れば分かるか |
|---|---|
| `capital` / `capital_etc` | 登記簿謄本、または定款（`capital_etc` は資本金等の額。均等割の区分に使う。通常は資本金＋資本準備金） |
| `established_on` | 登記簿謄本の会社成立の年月日 |
| `prior.taxable_sales` | 過去の決算書・消費税申告書（会計システムからは取れないことが多い。基準期間〈2期前〉の課税売上高は消費税の納税義務判定に必須） |
| `filings` の `unverified` | 受付印付きの控え、e-Taxの受信通知、承認通知書 |
| `officers` / `pay_day` | 役員報酬を出しているか。出していれば給与支払事務所の届出が要る。期中改定があれば `changed_on` に改定日（定期同額給与のチェックに使う） |
| `employees` | 従業員数（役員を除く）。**未確認の間は null のままにする**。0を入れると「0人と確認済み」の意味になり、年末調整・住民税特別徴収・労働保険の年度更新・算定基礎届などの確認が抜ける |
| `officers[].bonus` | 役員賞与（事前確定届出給与）を出すか。true / false を書くまで「未記録」として毎年判断が要る |

## 項目の意味

- `consumption_tax_method`：`standard`（本則）／`simplified`（簡易）／`exempt`（免税）
- `filing_extension`：申告期限の延長の特例（法人税・地方税）の届出有無。国・都道府県・市区町村の3か所とも出ているかを `filings` で確かめる
- `blue_return`：青色申告の承認。通知書の現物を確かめるまでは `filings.blue_return` を `unverified` のままにする
- `shareholders_meeting_month`：定時株主総会の月（事前確定届出給与の期限の計算に使う）

## 消費税の選択届出で見落とさないこと

課税事業者の選択・簡易課税の選択や取りやめ・インボイス登録は、**期限を過ぎると更正の請求では取り返せない届出**。免税事業者には課税事業者選択とインボイス登録を、簡易課税の会社には本則へ戻さなくてよいかを、課税期間の開始前（余裕をもって2か月前を目安）に必ず判断してもらう。出すと決めたら、期限と控えの保管先を `events` または `work/` に書く。
