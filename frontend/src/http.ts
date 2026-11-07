function servicePort(): string {
  const port = typeof window !== 'undefined' ? window.location.port : ''
  return port && port !== '5178' ? port : '8765'
}
function disconnected(): string {
  return `本機 ${servicePort()} 服務已停止或連線中斷。請開啟對應版本的啟動程式，再重新整理頁面；已儲存資料會保留。`
}
export async function localFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  try { return await fetch(input, init) }
  catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new Error(disconnected())
  }
}
export async function readJson<T = any>(response: Response): Promise<T> {
  try { return await response.json() as T }
  catch {
    throw new Error(response.ok
      ? `本機回應為空白或尚未傳送完整，可能在讀取時服務被停止。請啟動 ${servicePort()} 後重新整理；不要重複送出研究報告。`
      : `本機服務回應異常（HTTP ${response.status}）。請確認 ${servicePort()} 已啟動，再重新讀取。`)
  }
}
