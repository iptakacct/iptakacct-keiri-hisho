# 達人シリーズへの引き渡し：取り込み仕様メモ

- 調査日：2026-09-16
- ステータス：公開資料で調査済み／MF・freeeが達人へ渡すXMLを実物で確認（§3.5、2026-10-26）／kessan で作ったXMLの実機での読み込み試験待ち

## 0. 前提の訂正（重要）

公開資料を調べた結果、**「決算書の達人」という単独製品は、NTTデータの達人シリーズ公式製品一覧（14タイトル：法人税／減価償却／消費税／内訳概況書／所得税／年調・法定調書／相続税／贈与税／財産評価／事業所税／申請・届出書／グループ通算／データ管理／電子申告）に存在しない**ことを確認した。
- 出典：[達人シリーズ製品一覧トップ（tatsuzin.info）](https://www.tatsuzin.info/)、[法人税の達人｜NTTデータ](https://www.tatsuzin.info/products_hj/)

「決算書」の作成は、**「法人税の達人」の業務メニュー内にある「決算書の作成」機能**として提供されている（法人税の達人で決算書データを作成し、法人税申告書・内訳明細書・事業概況書と一緒に電子申告する）。そのため、以下 §1 は「決算書の達人」ではなく**「法人税の達人」の決算書作成機能**として整理した。

- 出典：[【法人税の達人】決算書の作成手順（3）※Excel・CSVデータ（達人独自形式）のインポート](https://www.occ21.co.jp/news/13951/)、[【法人税の達人】決算書の作成手順（5）※国税庁が提供しているCSV形式の標準フォーム](https://www.occ21.co.jp/news/19490/)、[【法人税の達人】決算書の作成手順（1）※連動コンポーネントによる取込](https://www.occ21.co.jp/news/13696/)、[【法人税の達人】決算書の作成手順（4）※手入力](https://www.occ21.co.jp/news/14224/)

## 1. 決算書の達人（＝法人税の達人「決算書の作成」機能）

- **他社データの読み込み：可能。確認できた方式は5パターン**
  1. 連動コンポーネントによる取込（他社会計ソフトの専用連動コンポーネント経由）
  2. XMLデータによる連動（達人シリーズ間、および他社ソフトの一部）
  3. Excel・CSVデータ（達人独自形式）のインポート（業務メニュー「ユーティリティ」→「データのインポート」→「中間ファイル（決算書用）からのインポート」）
  4. 手入力（帳票イメージの画面に直接入力）
  5. 国税庁が提供するCSV形式の標準フォーム（**「電子申告の達人」**側の「帳票」タブ→「帳票データ取込」→「財務諸表CSVファイル取込」から取込。Ver.2.0／Ver.3.0のどちらかを選択）
  - 出典：[決算書の作成手順（1）](https://www.occ21.co.jp/news/13696/)、[（3）Excel・CSVインポート](https://www.occ21.co.jp/news/13951/)、[（4）手入力](https://www.occ21.co.jp/news/14224/)、[（5）国税庁CSV標準フォーム](https://www.occ21.co.jp/news/19490/)、[法人税の達人｜連動ソフト・サービス](https://www.tatsuzin.info/products_hj/rendou.html)
- **列の並び・文字コード**：未確認。方式3（達人独自形式Excel/CSV）は記事中で「詳細は資料P.18〜P.30を参照」とされており、参照先の詳細マニュアル本文は今回の調査では確認できなかった。方式5（国税庁標準フォーム）は国税庁のCSV仕様（[財務諸表のCSV形式データの作成方法｜e-Tax](https://www.e-tax.nta.go.jp/hojin/gimuka/csv_jyoho4.htm)）に準拠すると推測されるが、達人側での列仕様との完全一致は未確認。
- **必要な製品・オプション：**
  - 方式1（連動コンポーネント）は「法人税の達人」のProfessional EditionまたはStandard Editionで利用可能（Light Editionでの可否は未確認）。出典：[連動ソフト・サービス｜法人税の達人](https://www.tatsuzin.info/products_hj/rendou.html)
  - 方式5（国税庁CSV標準フォーム取込）は「電子申告の達人」が別途必要（法人税の達人単体では不可）。出典：[（5）国税庁CSV標準フォーム](https://www.occ21.co.jp/news/19490/)
  - 方式2〜4に有償オプションが必要かは未確認。
- **科目の対応付け：**
  - 方式3（Excel・CSVインポート）では「インポートされた科目の設定先（法人税の達人で使用する科目）を変更することも可能」との記載あり。具体的なマッピングUI・自動対応ロジックの仕様は未確認。
  - 出典：[（3）Excel・CSVインポート](https://www.occ21.co.jp/news/13951/)

## 2. 法人税の達人（別表）

- **外部ファイルからの入力手段：** 「基本情報や各帳票等のデータを外部ファイル（Excel形式、CSV形式）からインポートできます」との記載を確認。マネーフォワード クラウド会計・減価償却の達人からのデータインポートで、法人税申告書のほか**別表十四（二）、別表十六関連帳票**を作成できるとの記載あり。個別の別表（例：別表四・五（一）等）ごとに外部ファイル入力が可能かどうかの網羅的な一覧は未確認。
  - 出典：[連動ソフト・サービス｜法人税の達人｜NTTデータ](https://www.tatsuzin.info/products_hj/rendou.html)
- **決算書の達人（＝決算書の作成機能）からの連動で入る項目：** 決算書データ作成後、そのまま法人税申告書・内訳明細書・事業概況書と合わせて電子申告できるとの記載あり。決算書のどの科目・金額が別表のどの欄に自動転記されるかの詳細な対応表は未確認。
  - 出典：[（1）連動コンポーネントによる取込](https://www.occ21.co.jp/news/13696/)

## 3. 内訳明細書の達人

- **製品名について：** 達人シリーズの公式ラインナップ上の製品名は「**内訳概況書の達人**」（16種類の勘定科目内訳明細書＋法人事業概況説明書＋会社事業概況書に対応）。
  - 出典：[内訳概況書の達人｜製品の特徴と価格表](https://www.occ21.co.jp/solution/tatsuzin/uchiwake/)
- **CSV取り込み：可否・形式は次の2系統を確認した。関係（同一機能か別機能か）は未確認。**
  1. 「内訳概況書の達人」自体に「規定の形式で作成したCSV形式のデータを取り込み」機能があるとの記載。具体的な列仕様・対応帳票の網羅リストは未確認。
     - 出典：[内訳概況書の達人｜製品の特徴と価格表](https://www.occ21.co.jp/solution/tatsuzin/uchiwake/)
  2. **「電子申告の達人」**側に「帳票」画面→「帳票データ取込」→「**勘定科目内訳明細書CSV取込**」があり、国税庁の標準フォーム（Excel）をベースにCSVを作成して取込む。**確認できた対応帳票は4種類のみ**：預貯金等、売掛金（未収入金）、棚卸資産、買掛金（未払金・未払費用）。内訳明細書は全16種類あるため、残り12種類がCSV取込に対応するかは未確認。
     - 出典：[【電子申告の達人】勘定科目内訳明細書(CSVファイル)は取込できますか？](https://www.occ21.co.jp/news/10228/)
- **必要な製品・オプション：** 「電子申告の達人」経由の取込には電子申告の達人が必要。「内訳概況書の達人」自体のCSV取込に別売オプションが必要かは未確認。

## 3.5 会計ソフトが出す達人用ファイルの実物（2026-10-26 調査）

会計ソフトは、達人へ渡すファイルを「達人の中間ファイル（XML）」で出している。kessan も同じ形を作れば、達人の同じ取り込みメニューに乗る見込み。

### マネーフォワード クラウド会計
- 出す場所：「決算・申告」→「達人シリーズ連携」。決算書（法人税の達人用）・内訳書・概況書（内訳概況書の達人用）・消費税（消費税の達人用、年度別3種）をそれぞれエクスポート
- 達人側：法人税の達人「業務メニュー」→「ユーティリティ」→「データのインポート」→「中間ファイル（決算書用）からのインポート」。その後「決算」→「決算書の作成」→「取込設定」で科目の対応を確認（未設定の科目は「ユーザー独自の科目」として入る。「1.売上高」は「総額表示」にする）
- 実物を取得して確認（2026-10-26、期首から半年分のデータがある会社）。エクスポートは画面のフォームが `GET /tatsujin/<種類>?month_search_from=YYYY/M&month_search_to=YYYY/M` を呼ぶだけで、種類は `tax_statement`（決算書）・`itemized_statement`（内訳書）・`summary_statement`（概況書、期間指定なし）・`excise_statement_sy35`／`_sy31`／（無印）（消費税 令和5年度以降／令和元年度以降／平成26年度以降）
- 形式はfreeeと同じ（Shift_JISのXML、同じ外枠・同じ `GYOUMUPREFIX`）。違いは `PROGRAMNAME` に製品名、`KOMONSAKINAME` に**会社名**が入り、`CONNECTEDPROGRAMVER`／`PROGRAMVER` が `1.0.0` であること。内訳書の `DATA` の構造もfreeeと同一で、残高0の科目は `KANJYOKAMOKU_ZANDAKA` が空
- 出典：[マネーフォワード クラウド会計 達人シリーズ連携](https://biz.moneyforward.com/support/account/?p=426)

#### 決算書（`GYOUMUPREFIX` HJ21・`FUNCTIONID` 3）の中身（MFの実物）
- `BASIC/PATTERN` に表示パターンのコードが入る（例：`Doubtful`=A2・C2、`DepImp`=B5・D5、`Sales`=E1、`Cos`=F3、`Sga`=G1）。各コードの意味は未確認（達人の「決算書の設定」の選択肢に対応すると思われる）
- `DATA` は3つ。金額は円の整数。区分の要素は `Label`（表示名）と `Amount`（区分合計）を属性で持ち、その下の `<Item Label="科目名">金額</Item>` が科目
  ```xml
  <BalanceSheet>
    <Assets Label="資産の部" Amount="…">
      <CurrentAssets Label="流動資産" Amount="…"><Item Label="現金及び預金">…</Item>…</CurrentAssets>
      <NoncurrentAssets Label="固定資産" Amount="…">
        <PropertyPlantAndEquipment Label="有形固定資産" Amount="0"/>
        <IntangibleAssets Label="無形固定資産" Amount="0"/>
        <InvestmentsAndOtherAssets Label="投資その他の資産" Amount="…"><Item …/></InvestmentsAndOtherAssets>
      </NoncurrentAssets>
      <DeferredAssets Label="繰延資産" Amount="0"/>
    </Assets>
    <Liabilities Label="負債の部" Amount="…">
      <CurrentLiabilities Label="流動負債" …/> <NoncurrentLiabilities Label="固定負債" …/>
    </Liabilities>
    <NetAssets Label="純資産の部" Amount="…"><Item Label="資本金">…</Item>…</NetAssets>
  </BalanceSheet>
  <IncomeStatement>
    NetSales / CostOfSales / GrossProfit / SellingGeneralAndAdministrativeExpenses / OperatingIncome /
    NonOperatingIncome / NonOperatingExpenses / OrdinaryIncome / ExtraordinaryIncome / ExtraordinaryLoss /
    IncomeBeforeIncomeTaxes / IncomeTaxes / NetIncome   （いずれも Label・Amount 属性、内訳は Item）
  </IncomeStatement>
  <StatementChangesNetAssets>
    <Select>1</Select>
    <ChangesNetAssetsItem>
      <AccountList>繰越利益剰余金</AccountList>
      <BalanceEndPreviousPeriod>…</BalanceEndPreviousPeriod>
      <ChangesItemsDuringPeriod>                       <!-- 変動がある科目だけ -->
        <TotalChangesItemsDuringPeriod>…</TotalChangesItemsDuringPeriod>
        <ChangesItem><Reason>当期純利益</Reason><Amount>…</Amount></ChangesItem>
      </ChangesItemsDuringPeriod>
      <BalanceEndCurrentPeriod>…</BalanceEndCurrentPeriod>
    </ChangesNetAssetsItem>
  </StatementChangesNetAssets>
  ```
- 科目名（`Item Label`）は会計ソフトの科目名のまま。達人に無い科目は取り込み時に「ユーザー独自の科目」になり、「取込設定」で対応付ける

#### 概況書（`GYOUMUPREFIX` UG16・`FUNCTIONID` 2）の中身（MFの実物）
- `DATA/SYUYOUKAMOKU`：法人事業概況説明書の「主要科目」欄に対応する固定の要素名（`URIAGE_SYUUNYUUDAKA` 売上高、`URIAGE_SYUUNYUUGENKA` 売上原価、`YAKUINHOUSYUU` 役員報酬、`JYUUGYOUINKYUURYOU` 従業員給料、`KOUSAIHI` 交際費、`EIGYOUSONEKI` 営業損益、`ZEIBIKIMAETOUKISONEKI` 税引前当期損益、`SISANNOBU` 資産の部合計、`GENKINYOKIN` 現金預金、`URIKAKEKIN` 売掛金、`KASITUKEKIN` 貸付金、`FUSAINOBU` 負債の部合計、`SONOTAKARIIREKIN` その他借入金、`SIHONNOBU` 純資産の部合計 など約40項目）。該当なしは空要素
- `DATA/TUKIBETUURIAGEDAKA`：月別の売上等。`_01`〜`_12` の連番で、月ごとに `TUKIBETU_nn`（月）・`URIAGESYUUNYUUKINGAKU1_nn`（売上）・`SIIREKINGAKU1_nn`（仕入）・`GAICHUUHI_nn`・`JINKENHI_nn`・`GENSENTYOUSYUUZEIGAKU_nn`（源泉徴収税額）など
- 消費税用（`excise_statement*`）も取得したが、kessan の対象（免税事業者）では使わないので構造は記録しない

### freee会計（実物を取得して確認）
- 出す場所：「決算申告」→「連携用データの作成」（`/tax_return/corporate`）。対象年度を選び、次の2つを「インポート用ファイルのダウンロード(XML形式)」で出す
  - 法人税の達人用の決算書データ：貸借対照表・損益計算書・株主資本等変動計算書（`houjinzei_no_tatsujin.xml?fy_id=…&use_corporation_tax_bs=…&use_corporation_tax_pl=…&use_corporation_tax_ss=…`）
  - 内訳概況書の達人用の勘定科目内訳明細書データ：内訳書16種類すべてが対象（`uchiwake_no_tatsujin.xml?fy_id=…`）
  - 消費税の達人用は2019-10-01以降終了の事業年度では不可（電子申告の達人へ e-Tax 用ファイルで渡す）
- 文字コード：**Shift_JIS**（XML宣言 `encoding="Shift_JIS"`）
- 共通の外枠：
  ```xml
  <REPORT>
    <SYSTEM>
      <GYOUMUPREFIX>HJ21</GYOUMUPREFIX>   <!-- 決算書=HJ21、内訳書=UG13 -->
      <VERSION>1</VERSION>
      <FUNCTIONID>3</FUNCTIONID>          <!-- 決算書=3、内訳書=1 -->
      <CONNECTEDPROGRAMVER>1.00.00</CONNECTEDPROGRAMVER>
      <PROGRAMNAME>freee</PROGRAMNAME>
      <PROGRAMVER>1.00.00</PROGRAMVER>
    </SYSTEM>
    <BASIC>
      <KOMONSAKINAME></KOMONSAKINAME>
      <JIGYOUNENDOFROM>YYYYMMDD</JIGYOUNENDOFROM>
      <JIGYOUNENDOTO>YYYYMMDD</JIGYOUNENDOTO>
      <!-- 決算書のみ：表示パターン -->
      <PATTERN>
        <BS><CA><Doubtful/></CA><PPE><DepImp/></PPE><IOA><Doubtful/><DepImp/></IOA></BS>
        <PL><Sales/><Cos/><Sga/></PL>
      </PATTERN>
    </BASIC>
    <DATA>…</DATA>
  </REPORT>
  ```
- 内訳書の `DATA`：**内訳書の行ではなく、勘定科目ごとの期末残高と、その補助科目ごとの残高の一覧**。どの内訳書に載せるかの振り分けは達人側で行う
  ```xml
  <KANJYOKAMOKU Count="1">            <!-- Count は1からの通し番号 -->
    <KANJYOKAMOKU_CODE>…</KANJYOKAMOKU_CODE>
    <KANJYOKAMOKU_NAME>受取利息</KANJYOKAMOKU_NAME>
    <KANJYOKAMOKU_ZANDAKA>1605</KANJYOKAMOKU_ZANDAKA>   <!-- 円・整数・符号付き -->
    <HOJYOKAMOKU Count="1">
      <HOJYOKAMOKU_CODE>…</HOJYOKAMOKU_CODE>
      <HOJYOKAMOKU_NAME>（口座名・取引先名）</HOJYOKAMOKU_NAME>
      <HOJYOKAMOKU_ZANDAKA>1605</HOJYOKAMOKU_ZANDAKA>
    </HOJYOKAMOKU>
  </KANJYOKAMOKU>
  ```
  BS・PLの科目が混在して並ぶ。`KANJYOKAMOKU_CODE` は freee の内部ID（科目の区分ごとに同じ値が重複する。普通預金は `0`）で、達人の科目コードではない
- 決算書の `DATA`：取得した会社（ひとり法人プラン）では、どの年度・どの指定でも**空**だった（決算書の作成が済んでいないと空になる可能性）。中身の構造はMFの実物で確認した（上の「決算書の中身」）

### kessan で作るもの（見込み）
- 決算書・内訳書・概況書の3つを、上の形のXML（Shift_JIS）で出せば、達人の既存の取り込みメニュー（MF・freeeと同じ経路）に乗る見込み。内訳書は科目・補助科目ごとの残高を出すだけでよく、kessan の帳簿（取引先・口座の列）から作れる
- 残る確認は、kessan で作ったXMLが実機で読めるか（`PROGRAMNAME` に未知の名前を入れて弾かれないか、科目名の対応付けがどう出るか）。§4 の1・2は、この実機での読み込み試験に置き換える

## 4. オーナーに実機で確認してほしいこと

1. 決算書の達人（＝法人税の達人の「決算書の作成」機能）の「データ読込」系メニューに、CSV等の汎用形式の選択肢があるか（あれば、画面に表示される形式の説明かサンプルファイルの保存）。特に、方式3「Excel・CSVデータ（達人独自形式）」の実際の列構成・文字コード・サンプルファイルを確認してほしい。
2. 内訳明細書（内訳概況書の達人）で取り込めるCSVのサンプルが出力できるか。特に、預貯金等・売掛金・棚卸資産・買掛金の4種類以外（その他12種類の内訳明細書）でもCSV取込に対応しているか。
3. 現在お使いの製品・エディション（法人税の達人／内訳概況書の達人／電子申告の達人 それぞれのProfessional・Standard・Lightのいずれか）と、連動コンポーネント等の有償オプションの有無一覧。

## 5. 結論（実機確認後に記入）

- 引き渡しファイルを作る対象：（未記入）
- 転記一覧のみにする対象：（未記入）
