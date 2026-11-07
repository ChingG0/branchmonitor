# 科技與金融公司債觀測

市場觀測的 `credit` 卡片由普通市場更新流程更新；GET 只讀已保存資料。V3、V6、V10 不會因此重新發布研究或持股。

十二家監控公司依序為 AVGO、AAPL、TSLA、MSFT、META、AMZN、GOOGL、NVDA、MU、TSM、ASML、JPM。十家有美元債利差；ASML 顯示歐元債價，沒有同幣別基準前不混算美元利差；Tesla 保留位置並標示尚未確認可比較且有公開報價的一般美元公司債，不用銀行授信、車貸 ABS 或股票波動率代替。

新增代表債券：

- MU：US595112BS19，2.703% 2032-04-15，半年付息。避開已於2026/7償還的5.3%2031年債（[Micron 2026年報](https://www.sec.gov/Archives/edgar/data/723125/000072312526000023/mu-20260903.htm)）。
- TSM：USG91139AH14，2.25% 2031-04-23，TSMC Global發行、台積電保證，半年付息。[SGX募集文件](https://links.sgx.com/FileOpen/TSMC%20Global%20Ltd.%20-%20OC%20dated%2020%20Apr%202021.ashx?App=Prospectus&FileID=51824)。固定Frankfurt公開報價，該券沒有gettex列；不取需登入的Stuttgart列。
- ASML：XS2473687106，EUR 2.25% 2032-05-17綠色債，年付息。僅顯示EUR債價及歷史，沒有YTM、美債或CDS換算。[ASML官方債券清單](https://www.investor.asml.com/investor-resources/capital-return-and-financing)。
- JPM：US46625HNJ58，4.25% 2027-10-01固定利率次順位債，半年付息。期限小於一年時，使用財政部6個月至1年曲線內插；介面顯著標示短期／次順位，不稱作5年優先債或CDS。[SEC定價文件](https://www.sec.gov/Archives/edgar/data/19617/000119312515327035/d188067dfwp.htm)。

## 資料與計算

- 債券資料：comdirect 公開頁面，逐檔核對 ISIN、發行人、幣別、固定票息、付息頻率、到期日。固定取設定場所的報價列（除TSM為Frankfurt，其餘gettex），並保存歐洲時區時間；不取可能多年未交易的預設交易所價格，也不取來源另列但基準價格不明的 `Rendite p.a.`。
- 同日基準：[美國財政部每日殖利率 XML](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=2026)。以實際剩餘日數／365.25，在上下相鄰年期間線性內插；不把隔日公債或政策利率混進去。這是對公布節點的自訂線性內插，不聲稱重現財政部完整曲線建構。
- 公司債 YTM：以報價日作為 T+0 估值日，依 30/360 US 應計息，對半年票息及到期本金求現值。首期長短息依發行日及第一付息日調整。輸出半年複利名目年率，便於與財政部 bond-equivalent 年率比較。未模擬實際交割日、交易成本及提前贖回。
- 利差 bp＝（公司債 YTM 百分比數字 − 同日對應年期公債百分比數字）×100。這是報價推估的 G-spread，不是成交利差、CDS、Z-spread 或 OAS；兩地取價時點仍可能不同。
- 歷史資料：從所選場所的`ID_NOTATION`取得comdirect三個月公開圖表`chartQuoteInfo`，去除像素對應的同日重複值；同日多值不一致、圖表最後日期／價格不符指定場所時拒絕整份圖表。不取未來日期。每筆歷史價格按日期配對當日財政部曲線重算利差；缺同日曲線則略過，保留最近60來源日。不將事後回溯資料描述為當時已發布的預警。
- 後續按來源日期去重累積。同日重新擷取只修訂當日，不製造一筆「昨日」。改債券或交易場所、計算版本時重開序列；來源倒退時保留舊值並顯示原因。歷史圖表失敗不使當日報價失敗，保留已有同券同場所歷史並顯示提醒。
- 某家失敗只保留該家上次值與原日期；沒有與報價同日的公債時不混日計算。超過七個日曆天的報價標示較舊。
- 歐洲已開始新交易日但美債當日曲線尚未公布時，從同一券種、同一場所的已核對歷史圖表，選最近有同日曲線的一天。介面明示配對日期、歷史日資料及尚待配對的最新報價日期；不把盤中債價搭配昨天美債，也不假造歷史報價時刻。找不到匹配日期時仍保留最後有效紀錄。

微軟目前選用 3.50% 2035-02-12（US594918BC73），年期較其他代表債券長，介面明列年期；不以這八個原始利差替公司做信用等級排名，也不任意套用股災警戒門檻。

## 驗證來源

- [NVIDIA 2031 Notes 契約與首期付息](https://www.sec.gov/Archives/edgar/data/1045810/000119312526275783/d48176dex42.htm)
- [NVIDIA 2026/6 定價文件](https://www.sec.gov/Archives/edgar/data/1045810/000119312526271326/d49394dfwp.htm)：發行價格 99.810、YTM 4.543%、基準 4.193%、T+35bp。估算器以發行日價格重算為 4.543063%，此為有已知答案的驗算，不把發行利差當最新值。
- [財政部利率口徑 FAQ](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/interest-rates-frequently-asked-questions)
- [Microsoft 2026 年報債務說明](https://www.microsoft.com/investor/reports/ar26/)
- [Tesla 2026/9 授信揭露](https://ir.tesla.com/_flysystem/s3/sec/000162828026063820/000162828026063820-gen.pdf)

驗證：`PYTHONPATH=.:tests .venv/bin/python -m unittest test_credit_spreads test_market_monitor test_market_comparison test_market_all_comparison -q`、既有 market 前端測試及專案根目錄 `npm run build`。實際瀏覽器核對十二家公司、十筆美元利差、ASML歐元價格、Tesla缺項、歷史圖表滑鼠及鍵盤操作、來源日期、窄版與唯讀檢視。JPM發行價99.655已知YTM4.287%，估算重現4.286955%。

## 5年CDS參考表

獨立於公司債G利差，沒有任何公司實際CDS報價就不顯示該公司CDS或違約機率。使用者提供的50／100／200／500bp門檻保留為參考，但文字不宣稱對應信用評級。簡化固定hazard、40%回收率時，`PD(5)=1-exp(-(CDS_bp/10000)/0.6*5)`；門檻約4.08%、8.00%、15.35%、34.08%。這是風險中性定價隱含機率，不是實際違約發生率。

參考：[S&P/ISDA指數方法附錄](https://www.spglobal.com/spdji/en/documents/methodologies/methodology-sp-isda-cds-indices.pdf)、[芝加哥聯準銀行對CDS隱含機率的解釋](https://www.chicagofed.org/publications/economic-perspectives/2023/4)。
