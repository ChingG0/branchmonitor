"""CLI notification only; report/GET and publish functions do not import this."""
import sys


def notify(model):
    try:
        from scripts.drive_backup import notify as queue_notification
        queue_notification(model)
    except Exception as exc:
        print('策略已保存；備份通知失敗，請在對話完成備份：' + str(exc), file=sys.stderr)
