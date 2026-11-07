# GitHub程式備份與資料還原

儲存庫：https://github.com/ChingG0/branchmonitor

本儲存庫保存程式、策略設定、必要靜態資源、測試與文件。不包含完整行情資料庫、`data/`、`research/`、原始來源檔、即時預報匯出JSON、Python／Node套件、`dist/`或金鑰。GitHub上傳成功不代表完整市場與帳戶資料已離開這台電腦。Google Cloud資料封存與同步尚未建置。

## 已建立的本機完整副本

首次GitHub上傳前，完整副本位於：

`/Users/ching/Desktop/branch-monitor-backup-before-github-20261107-132626`

副本包含當時的資料、研究、程式與依賴，根目錄`BACKUP-MANIFEST.json`列出檔案SHA256與資料庫檢查結果。使用APFS寫入時複製：原檔與副本可獨立修改，但初始共用儲存區塊；它仍在同一顆硬碟，無法防範整顆硬碟故障。

## 從GitHub還原程式

```bash
git clone https://github.com/ChingG0/branchmonitor.git
cd branchmonitor
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci
npm run build
```

首次安裝需要相容的Python及Node.js；本機環境使用Python3.11與Node24。啟動程式之前，先從可信任的完整備份還原`data/`；需要回測與研究時一併還原`research/`及原型看板資料。使用不完整資料啟動不會自動重建原有持股、策略帳本與預報紀錄。

手動預報的四個匯出JSON不入Git。請從完整副本還原`frontend/public/v6-research/`後再執行`npm run build`；如已先建立前端，還原靜態匯出檔後須再建立一次。只有唯讀export可以重新匯出已保存決策，不可用publish重建舊預報。

完整資料還原後可雙擊`啟動監控.command`。若已有其他副本在8765執行，請先停止原服務，避免開到另一份資料。

## 後續更新

程式改動以Git提交並推送，資料備份另行進行。V3／V6／V10／V11仍依對話手動發布；Git提交、推送、拉取與重新整理頁面都不應發布策略、核對交易或重算預報。還原資料時保留原資訊截點、保存時間與追加帳本，不把還原時間當成新的發布。

## 未完成的雲端資料保護

Google Cloud同步、資料分區、增量備份與還原演練仍須另行建置。在完成前，完整資料只保存在本機及上述同磁碟副本，應另有外接硬碟或雲端完整備份。
