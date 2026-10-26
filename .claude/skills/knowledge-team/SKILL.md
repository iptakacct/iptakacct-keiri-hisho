---
name: knowledge-team
description: 「経理ルールをまとめて」「仕訳ルールを整理して」のように、この会社（このAI秘書インスタンスが専属で担当している経理BPOクライアント）自身の経理ルール・仕訳ルールの整備を頼まれたら使う。情報収集→分類整理→ドキュメント化→整合チェックの順で、Agent Team（knowledge-collector→knowledge-classifier→knowledge-documenter→knowledge-reviewer）に分担させる。
---

# 目的

この会社について散らばっている経理ルール・仕訳ルール（主にSlackでのやりとり）を集め、いつでも参照できる正式ドキュメントとして `context/company/` に整備する。

（このAI秘書インスタンスは、経理BPOクライアント1社専属を想定している。複数クライアントを1インスタンスでまとめて扱う場合は、`context/customers/<クライアント名>/accounting-rules.md` に読み替える。）

# 手順

1. Agent Team を直列で起動する（Agent ツールで、前工程の出力を次工程の入力として渡す）。
   1. `knowledge-collector` — Slack等から関連するやりとりを収集
   2. `knowledge-classifier` — トピック別に分類整理
   3. `knowledge-documenter` — 正式ドキュメントに整える
   4. `knowledge-reviewer` — 通しで批判的にチェック
2. `knowledge-reviewer` の指摘（矛盾・要確認項目）があれば、オーナーに見せて判断を仰ぐ。
3. 確定した内容を `context/company/accounting-rules.md` に保存する。

# 出力フォーマット

```
【経理ルール整備】

■ ドキュメント案
（context/company/accounting-rules.md の内容）

■ レビュー結果（knowledge-reviewerより）
- 指摘：…（無ければ「確認した上で指摘なし」）

■ 要確認（オーナーの判断待ち）
- …
```

# 注意

- 出典の無い内容、矛盾する内容を、断定的なルールとして書かない。

# Gotchas（過去の失敗メモ）
- 入力が大きい（取り込み済みのSlackログ数十万字など）ときは、`knowledge-collector` を会社・チャンネル単位で並行に複数起動する。**収集担当の報告は親の会話にしか残らず（記録ファイルは空）、分類担当へ渡すには書き写しが要る**。報告を読んだ親が分類・文書化を担い、`knowledge-reviewer` だけを別に立てる方が重複が少ない
- **`knowledge-reviewer` には既存文書と原文の両方を渡し、原文を最低15箇所引いて確かめさせる**。要約した親は、税込/税抜の向き・「検討中」を決定と書く・管理会計PLの話を会計帳簿のルールとして書く、といった取り違えを自分では見落とす（実際に重大4件・中15件の指摘があった）
- **ログそのものに認証情報（ネットバンキングのID・パスワード、カード番号、2段階認証コード）が書かれていることがある**。生ログをgitに入れる前に確かめる。収集担当には「識別番号は写さず伏せる」を必ず指示する
