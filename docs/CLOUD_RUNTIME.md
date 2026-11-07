# 雲端資料、本機執行

程式由GitHub clone，資料來源為Google Drive桌面串流。原本本機版維持8765；獨立雲端版固定http://localhost:3001。改網址本身不代表雲端模式，health及側欄依實際資料來源顯示。

## 本機位置與開啟

- 雲端程式：`/Users/ching/Desktop/branch-monitor-cloud`，獨立GitHub clone及自己的Python／Node依賴，不連到舊專案資料。
- 雙擊該資料夾的`啟動雲端監控.command`，也可用一般`啟動監控.command`自動辨識雲端設定。停止使用同資料夾的`停止雲端監控.command`。
- 每台電腦以`python3 -B scripts/cloud_runtime.py configure`偵測已登入的Drive串流；`cloud-runtime.local.json`含該機路徑，不入Git。預設工作暫存`~/Library/Caches/BranchMonitorCloud/workspace`。
- 第一次安裝仍需Python、Node及`npm ci`；啟動器可建立venv、安裝requirements及建置前端。資料先通過雲端SHA256／SQLite檢查後才啟動，不使用空資料庫冒充成功。
- 雲端啟動依「每日自動更新」設定執行首頁同一流程，預設週一至週五台北時間18:00；來源有缺漏每小時重試。需要程式運行、Mac喚醒、網路及Drive桌面版可用，關機／休眠不能執行，18:00後開啟可補跑當日。首頁與排程皆不發布V3/V6/V10/V11或核對其成交。一般更新先保存在工作暫存，未完成下述雲端讀回校驗前，不代表已上雲。

## 雲端與快取

Drive資料夾為`Branch Monitor 備份/70-雲端運行資料`，ID `1myeM28cbvlUIjzOtOFv-6ulfmxMlfmDI`。`CURRENT.json`指向含SHA256的BASE-CATALOG及已驗證增量層；完整基底仍使用已讀回校驗的`20261107T140242-首次完整備份`，不重傳約2.58GB。

目錄涵蓋390,340個原檔，按當初封裝規則重建檔案與壓縮包對照，與21包的筆數及位元組逐項吻合。程式經Drive串流取得壓縮包，逐片及全包校驗，僅解出所需檔案，SQLite使用本機工作副本。主庫約875MB、融資來源約637MB等必要運算資料仍會占用本機，不能承諾零占用。

啟動先取得日常資料、族群、已存預報與V6必要研究；381,424個`data/raw`歷史原檔、舊程式副本、較大的封存研究及舊V11輸入留在雲端。融資分頁只讀已保存報表，不下載重跑研究用的凍結輸入；另做研究時才明確取用。`Runtime.ensure([相對路徑])`可另按需取指定原檔。這不是全部資料一律離線，也尚未自動清除工作快取；Drive本身亦有串流快取。未驗證寫回雲端前不得刪除工作資料。

新clone中的`data`及`research`只連獨立工作暫存，並非舊`branch-monitor`。族群資料及已存前台匯出以忽略檔連結接入，GitHub源碼不被資料覆寫；相關路徑及本機設定排除Git提交。讀取Drive失敗或新舊版本衝突時明確停止，不能回頭使用舊專案資料或建立空帳戶。

## 手動更新後保存到Drive

1. V3/V6/V10/V11仍只能依使用者在對話的要求，在雲端clone執行原有CLI發布。原儲存時間與追加帳本不變。成功CLI排入該工作暫存的`control/queue`，不呼叫舊包5全量掃描（冷資料未下載不代表已刪除）。
2. 一批更新完成執行`.venv/bin/python -B scripts/cloud_runtime.py prepare-sync`。僅掃描已取得的工作檔案及新匯出，SQLite用backup API一致性快照，再壓縮成8MiB區塊；未取得冷檔保持雲端原值，不產生刪除記錄。`PENDING.json`列出雲端相對路徑、大小及SHA。此時只代表交給Drive桌面程式待同步，**不代表雲端保存完成**。中斷先續同一PENDING，不能跳版。
3. 以對話內Drive工具逐檔找到相同目錄／路徑，核對私人權限，再實際下載讀回計算SHA256。產生收據：`{verifiedAt,ownerOnly:true,files:[{path,fileId,bytes,readBackSha256}]}`。不得用本機串流檔剛寫完的SHA假裝雲端讀回。
4. 執行`.venv/bin/python -B scripts/cloud_runtime.py confirm --receipt <收據絕對路徑>`。核對父版及全部讀回結果後，才追加層並更新Drive的CURRENT；再讀回雲端CURRENT確認同步，並重啟3001使health取得最新版本。最後上傳的指標仍需Drive同步，不得提前宣稱完成。
5. 保存失敗不回滾已存策略，但必須明確報告待同步。20GB專案預算包含基底與運行版本，超限停止，不購買容量、不刪舊版。Google One實際剩餘空間仍須另查。

只支援單一寫入者。另一台或另一個程序改了CURRENT時拒絕覆寫；本機未同步變更須先處理。原始資料、備份、已存預報不因快取或上雲而刪除。完整雲端寫回、跨機還原與冷資料全量還原的驗收應分別記錄，不能把小型單元測試或熱資料啟動當成全量還原演練。
