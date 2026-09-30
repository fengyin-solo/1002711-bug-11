/** 统一请求封装：拼后端地址、抛网络错误、给页脚留一句可读的说明。 */
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export function request(path: string, init?: RequestInit): Promise<Response> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`
  return fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  }).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : '请求未送达'
    throw new Error(`接口请求失败：${detail}`)
  })
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return (await response.json()) as T
}

/** 原样提取服务端报错：FastAPI 的 detail 或 ActionResult.message 都直接带出，不覆盖成笼统提示。 */
export async function extractError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown; message?: unknown }
    if (typeof payload.message === 'string' && payload.message) {
      return payload.message
    }
    if (typeof payload.detail === 'string' && payload.detail) {
      return payload.detail
    }
    if (Array.isArray(payload.detail) && payload.detail.length > 0) {
      return payload.detail.map((item) => JSON.stringify(item)).join('；')
    }
  } catch {
    // 响应体不是 JSON 时落到状态码提示
  }
  return `接口返回 ${response.status}，数据未更新`
}
