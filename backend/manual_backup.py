"""CLI notification only; report/GET and publish functions do not import this."""
import sys


def notify(model):
    try:
        from pathlib import Path
        if (Path(__file__).resolve().parents[1]/'cloud-runtime.local.json').is_file():
            from scripts.cloud_runtime import instance
            instance().queue(model)
            print('已排入雲端運行資料同步；尚待Drive上傳及讀回確認',file=sys.stderr)
            return
        from scripts.drive_backup import notify as queue_notification
        queue_notification(model)
    except Exception as exc:
        print('策略已保存；備份通知失敗，請在對話完成備份：' + str(exc), file=sys.stderr)
