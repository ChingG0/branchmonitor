# 大盤預估本益比河流圖：包1、2

後續包3的計算、資格與首次試算見[包3說明](FORWARD_PE_PACKAGE_3.md)。以下保留包1、2完成時的範圍與來源驗收紀錄。

本階段完成來源盤點、不可覆寫資料層、首次數據保存、匯入範本與唯讀API。尚未製作河流圖／側欄分頁、估值計算、位階或任何交易訊號，也未加入首頁排程。

## 本次來源驗收結果

| 項目 | 本次結果 | 口徑與限制 |
|---|---|---|
| 官方成分股與權重 | 期交所1075檔，取前50 | 2026-10-30月底資料；前50占81.1679%，不是11/6即時權重 |
| 股價 | 前50共50檔 | 已保存的2026-11-06官方未還原收盤 |
| 發行股數 | 前50共50檔 | 與股價同為2026-11-06；原始股數，不是千股或自由流通股數 |
| 可核對公開年度EPS | 8／50檔 | 來自11份鉅亨FactSet公開報導，包含歷次版本；不是完整或隨機樣本 |
| EPS權重涵蓋 | 大盤9.3579%、前50籃子11.5291% | 使用10/30官方權重計算涵蓋，未當作目前大盤估值 |
| 共識口徑資格 | 0檔已完整核對 | 基本／稀釋、股數調整、原供應商預估時刻尚未核對；不等於8檔數值皆錯 |
| NTM | 0檔 | 不以今年、明年EPS任意混成未來12個月 |
| 過去5年當時預估版本 | 尚未取得 | 今天採集的舊文章不當成過去已保存的每日預估 |

已保存年度資料的8檔為聯發科2454、日月光投控3711、景碩3189、創意3443、光寶科2301、玉山金2884、研華2395、緯穎6669。資料庫72筆EPS列包含多年度、平均／中位數與歷次報導，不能當成72家公司。台積電目前缺少本次範圍內合格共識來源，不以單家券商推估或目標價替代。

包1的**盤點已完成**，但完整估值所需的**來源資格未通過**。包2的保存、匯入、時間隔離及版本保留可用。不能因程式完成，就宣稱全大盤預估估值或5年位階已可使用。

## 來源與實際查核

- [期交所成分股與比重](https://www.taifex.com.tw/cht/2/weightedPropertion)：每月月底更新。資料日期從頁面擷取，未用取得日冒充。
- 行情及股數沿用`data/margin_statistics/sources.sqlite3`內當日官方`MI_INDEX`、`MI_QFIIS`保存。只開唯讀連線，另存抽取證據、父來源SHA及原始取得時間，不重跑融資研究、不改主庫。
- [TWSE官方本益比](https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL)：可讀，但不是預估EPS，隔離為參考來源。
- [FactSet時點共識商品](https://www.factset.com/marketplace/catalog/product/factset-estimates-pointintime)：有相應產品，不代表本系統取得授權。正式API本次導向登入頁，未嘗試登入或繞過。
- Yahoo預估介面本次回傳429，停止查核；未換主機或反覆請求。
- [鉅亨預估頁](https://www.cnyes.com/twstock/2330/research/finirating)：目標價及評等有值，但年度EPS顯示空白。目標價、TTM EPS不替代年度預估。
- [鉅亨公開報導例](https://news.cnyes.com/news/id/6643516)：只解析「市場預估EPS」表；營收與歷史獲利表分開，後者即使列名EPS也不採用。新聞中的前值只是同篇報導記載，沒有被倒填為另一筆較早保存紀錄。
- 公開新聞來源屬有限探索，部分連結由偏上修消息的索引發現，仍逐份回原始報導取數；不能推論全市場上修分布。RSS本次為空，未宣稱查遍所有50檔。
- [ToAlpha公開文件](https://toalpha.tw/mcp-docs)明示共識工具僅提供衍生比率；未以衍生比率反推原始EPS。網站公開頁的原始共識供應商、基本／稀釋與歷史版本未獨立核對，未採為本次正式估值來源。

## 保存結構與時間

`data/forward_pe/observations.sqlite3`包含sources、universes、quotes、estimates、snapshots、audits；所有表禁止UPDATE／DELETE。來源原檔存`raw/<SHA256>.gz`，來源版本及快照只追加。

- `effective_date`／`market_date`：權重或行情代表哪一天。
- `forecast_as_of`：供應商明示的預估日期；新聞未提供就保留缺漏。
- `published_at`：原始報導發布時刻，不以資料年度或匯入時間代替。
- `retrieved_at`：本次取得／讀取來源證據的時間；重用本機原檔不代表再次網路查核，父來源原始取得時刻另存metadata。
- `ingested_at`及`known_at`：本資料層實際入庫與可用時間，由程式產生。原始取得時刻即使較早，也不能把入庫可用日倒填。

讀取截止時刻須同時通過`published_at <= cutoff`與`known_at <= cutoff`。晚到的舊報導不能取代較新的供應商版本。年度、NTM、均值、中位數、來源及每股口徑分開；同版本有不同基本／稀釋口徑時須明確選擇，不能按雜湊排序亂選。

## 匯入與查閱

範本在`templates/forward_pe/eps_import.csv`與`source_manifest.json`，空白範本不含虛構EPS。每列須有代碼、期間、均值／中位數、TWD、每股口徑、發布時間及來源。NTM必須由供應商明示起訖；不以年度EPS轉製。負EPS與0照存，缺值不填0。匯入同批有一筆不符就不寫入EPS列。

```bash
.venv/bin/python -B -m backend.forward_pe_store init
.venv/bin/python -B -m backend.forward_pe_store add-source --raw <原始匯出檔> --provider licensed_export --url <有權使用的來源網址>
.venv/bin/python -B -m backend.forward_pe_store import-eps --csv <依範本整理的檔案> --source-id <上一步來源ID>
.venv/bin/python -B -m backend.forward_pe_inventory report
```

來源時間混合的整包匯出不用給單一`--published-at`，各列保存其原始發布時間。不可為了通過資格虛填股數調整或基本／稀釋證據。

唯讀API：`GET /api/forward-pe/inventory?cutoff=<含時區時刻>`、`GET /api/forward-pe/snapshot?id=<快照ID>`。GET不下載、不建空庫、不計算估值、不發布任何策略。

首次完整清單與來源檢查存於`data/forward_pe/exports/279fcb4f823f4e7a87fd9c2de131f2f1/`。方法規則固定於`config/forward_pe/protocol.json`；包3正式聚合前需完成來源補齊與每股口徑核對。資料目前存於Cloud獨立工作快取；尚未執行Drive增量讀回校驗，不宣稱已上雲。程式碼、範本及說明可同步GitHub，原始報導與行情不提交公開儲存庫。
