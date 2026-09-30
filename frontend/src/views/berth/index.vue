<template>
  <section class="page" data-module="berth">
    <header class="page-head">
      <div>
        <h2>泊位计划管理</h2>
        <p class="page-desc">围绕泊位编号、长度、水深、可停吨位做登记与靠泊分配；占用随分配明细实时重算。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记泊位</button>
        <button class="btn" type="button" @click="exportRows">导出泊位计划清单</button>
      </div>
    </header>

    <!-- 泊位总览：数字全部取自 /api/berth/occupancy，与表格明细同一口径 -->
    <div class="stat-row">
      <article v-for="item in statsCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>泊位编号</span>
        <input v-model="keyword" placeholder="按泊位编号检索" />
      </label>
      <label class="filter-item">
        <span>泊位状态</span>
        <select v-model="statusFilter">
          <option value="">全部</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <!-- 服务端报错原样带出，并保留重试入口 -->
    <div v-if="errorMessage" class="banner error">
      <span>{{ errorMessage }}</span>
      <button v-if="lastFailed" class="link" type="button" @click="retryLast">重试</button>
      <button class="link" type="button" @click="errorMessage = ''">收起</button>
    </div>
    <div v-if="successMessage" class="banner success">
      <span>{{ successMessage }}</span>
      <button class="link" type="button" @click="successMessage = ''">收起</button>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>已排时段</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <!-- 所有行始终渲染：字段缺口以「待补充（缺：X）」点明，不再整块留白/消失 -->
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <span v-if="isMissing(row, column)" class="cell-missing">
              待补充<template v-if="missingTip(row, column)">（缺：{{ missingTip(row, column) }}）</template>
            </span>
            <span v-else>{{ displayValue(row, column) }}</span>
          </td>
          <td>
            <ul v-if="row.分配明细 && row.分配明细.length" class="window-list">
              <li v-for="(item, idx) in row.分配明细" :key="idx">
                {{ item.船名 }}：{{ fmtWindow(item.靠泊开始) }} ~ {{ fmtWindow(item.离泊结束) }}
              </li>
            </ul>
            <span v-else class="cell-missing">待排</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openAllocate(row)">分配靠泊</button>
            <button class="link" type="button" @click="runSimpleAction('释放泊位', row)">释放泊位</button>
            <button class="link" type="button" @click="runSimpleAction('登记维护', row)">登记维护</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无泊位计划数据，可先登记泊位</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条泊位计划记录</span>
    </footer>

    <!-- 分配靠泊：datetime-local 天然支持跨天；提交带幂等令牌，失败保留输入并可重试 -->
    <div v-if="allocate.open" class="modal-mask" @click.self="closeAllocate">
      <form class="modal" @submit.prevent="submitAllocate">
        <h3>分配靠泊 · {{ allocate.row?.泊位编号 }}</h3>
        <p v-if="allocate.hint" class="modal-hint">{{ allocate.hint }}</p>
        <label>靠泊船名 *
          <input v-model="allocate.form.船名" placeholder="如：远东之星" />
        </label>
        <div class="form-grid">
          <label>船舶吃水（米）*
            <input v-model="allocate.form.吃水" inputmode="decimal" placeholder="如：10.8" />
          </label>
          <label>船舶长度（米）
            <input v-model="allocate.form.船长" inputmode="decimal" placeholder="如：210" />
          </label>
          <label>船舶吨位（吨）
            <input v-model="allocate.form.吨位" inputmode="numeric" placeholder="如：75000" />
          </label>
        </div>
        <div class="form-grid two-col">
          <label>靠泊开始 *
            <input v-model="allocate.form.靠泊开始" type="datetime-local" />
          </label>
          <label>离泊结束 *（可跨到第二天）
            <input v-model="allocate.form.离泊结束" type="datetime-local" />
          </label>
        </div>
        <p v-if="allocate.error" class="error-text">{{ allocate.error }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeAllocate">取消</button>
          <button class="btn" type="button" :disabled="allocate.submitting" @click="submitAllocate">
            {{ allocate.submitting ? '提交中…' : '提交分配' }}
          </button>
        </div>
      </form>
    </div>

    <!-- 登记泊位 -->
    <div v-if="create.open" class="modal-mask" @click.self="create.open = false">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记泊位</h3>
        <label>泊位编号 *
          <input v-model="create.form.泊位编号" placeholder="如：BERT-0007" />
        </label>
        <div class="form-grid">
          <label>泊位长度（米）*
            <input v-model="create.form.泊位长度" inputmode="decimal" />
          </label>
          <label>水深条件（米）*
            <input v-model="create.form.水深条件" inputmode="decimal" />
          </label>
          <label>可停吨位（吨）
            <input v-model="create.form.可停吨位" inputmode="numeric" />
          </label>
        </div>
        <p v-if="create.error" class="error-text">{{ create.error }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="create.open = false">取消</button>
          <button class="btn primary" type="submit">保存</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, unknown> & {
  id: number
  缺失字段?: string[]
  分配明细?: { 船名: string; 靠泊开始: string; 离泊结束: string }[]
}

type Occupancy = Record<string, number | string | { 泊位编号: string; 靠泊船名: string }[]>

const ENDPOINT = '/api/berth'
const columns = ['泊位编号', '泊位长度', '水深条件', '泊位类型', '可停吨位', '靠泊船名', '靠泊时段', '离泊时段', '泊位状态']
const statuses = ['空闲', '已靠泊', '维护中', '不可用']

const rows = ref<Row[]>([])
const total = ref(0)
const occupancy = ref<Occupancy>({})
const errorMessage = ref('')
const successMessage = ref('')
const lastFailed = ref<(() => Promise<void>) | null>(null)
const keyword = ref('')
const statusFilter = ref('')

const statsCards = computed(() => [
  { label: '泊位总数', value: num('泊位总数') },
  { label: '占用泊位', value: num('占用泊位数') },
  { label: '空闲泊位', value: num('空闲泊位数') },
  { label: '维护泊位', value: num('维护泊位数') },
  { label: '深水泊位', value: num('深水泊位数') },
  { label: '占用率', value: String(occupancy.value['占用率'] ?? '0%') },
])

function num(key: string): number {
  const value = occupancy.value[key]
  return typeof value === 'number' ? value : 0
}

// ---- 单元格缺口展示：缺项点明，不让行整块消失 ----
function isMissing(row: Row, column: string): boolean {
  if (column === '泊位类型') {
    return (row['缺失字段'] ?? []).includes('水深条件')
  }
  const value = row[column]
  return value === undefined || value === null || value === '' || value === '待补充'
}

function missingTip(row: Row, column: string): string {
  const missing = (row['缺失字段'] as string[] | undefined) ?? []
  if (column === '泊位类型') {
    return missing.includes('水深条件') ? '水深条件' : ''
  }
  // 靠泊时段 / 离泊时段 / 靠泊船名不是主数据，缺口来自尚未安排分配
  if (['靠泊时段', '离泊时段', '靠泊船名'].includes(column)) {
    return ''
  }
  return missing.includes(column) ? column : ''
}

function displayValue(row: Row, column: string): string {
  const value = row[column]
  return value === undefined || value === null ? '' : String(value)
}

function fmtWindow(value: string): string {
  // 跨天时段完整保留日期，避免只显示时分让人误判
  return value ? value.replace('T', ' ') : '待补充'
}

// ---- 数据加载：列表与占用看板一起刷新 ----
async function reload() {
  errorMessage.value = ''
  lastFailed.value = null
  const query = new URLSearchParams()
  if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
  if (statusFilter.value) query.set('status', statusFilter.value)

  async function task() {
    const [listRes, occRes] = await Promise.all([
      request(`${ENDPOINT}?${query.toString()}`),
      request(`${ENDPOINT}/occupancy`),
    ])
    if (!listRes.ok) {
      throw new Error(await readError(listRes, '泊位列表读取失败'))
    }
    if (!occRes.ok) {
      throw new Error(await readError(occRes, '泊位占用读取失败'))
    }
    const payload = await listRes.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    occupancy.value = await occRes.json()
  }

  try {
    await task()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '泊位计划列表读取失败'
    lastFailed.value = () => runGuarded(task, 'reload')
  }
}

async function readError(response: Response, fallback: string): Promise<string> {
  // 服务端给的报错原样带出：优先取 detail / message，不用通用文案盖掉
  try {
    const data = await response.json()
    if (typeof data.detail === 'string' && data.detail) return data.detail
    if (typeof data.message === 'string' && data.message) return data.message
  } catch {
    /* 非 JSON 响应走下面 */
  }
  return `${fallback}（HTTP ${response.status}）`
}

async function runGuarded(task: () => Promise<void>, key: string): Promise<void> {
  errorMessage.value = ''
  try {
    await task()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '泊位计划操作失败'
    lastFailed.value = () => runGuarded(task, key)
  }
}

async function retryLast() {
  const retry = lastFailed.value
  if (retry) await retry()
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

// ---- 简单动作：释放 / 维护 ----
async function runSimpleAction(action: string, row: Row) {
  errorMessage.value = ''
  async function task() {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const data = await response.json().catch(() => null)
    if (!response.ok || !data?.ok) {
      throw new Error(data?.message || data?.detail || `泊位动作「${action}」未生效（HTTP ${response.status}）`)
    }
    successMessage.value = data.message
    await reload()
  }
  await runGuarded(task, action)
}

// ---- 登记泊位 ----
const create = reactive({
  open: false,
  error: '',
  form: { 泊位编号: '', 泊位长度: '', 水深条件: '', 可停吨位: '' },
})

function openCreate() {
  create.open = true
  create.error = ''
  create.form = { 泊位编号: '', 泊位长度: '', 水深条件: '', 可停吨位: '' }
}

async function submitCreate() {
  create.error = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...create.form } }),
    })
    const data = await response.json().catch(() => null)
    if (!response.ok || !data?.ok) {
      create.error = data?.message || data?.detail || '泊位登记失败'
      return
    }
    create.open = false
    successMessage.value = data.message
    await reload()
  } catch (error) {
    create.error = error instanceof Error ? error.message : '泊位登记失败'
  }
}

// ---- 分配靠泊 ----
const allocate = reactive({
  open: false,
  submitting: false,
  error: '',
  hint: '',
  requestId: '',
  row: null as Row | null,
  form: { 船名: '', 吃水: '', 船长: '', 吨位: '', 靠泊开始: '', 离泊结束: '' },
})

function openAllocate(row: Row) {
  allocate.open = true
  allocate.submitting = false
  allocate.error = ''
  allocate.row = row
  allocate.requestId = newRequestId()
  allocate.form = { 船名: '', 吃水: '', 船长: '', 吨位: '', 靠泊开始: '', 离泊结束: '' }
  allocate.hint = `泊位水深 ${row['水深条件']}，长度 ${row['泊位长度']}，可停吨位 ${row['可停吨位']}`
}

function newRequestId(): string {
  // 幂等令牌：优先用 UUID，非安全上下文下回退到时间戳随机串
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `berth-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function closeAllocate() {
  if (allocate.submitting) return
  allocate.open = false
}

function toLocalInput(value: string): string {
  // '2026-09-02 22:00' -> '2026-09-02T22:00'
  return value ? value.replace(' ', 'T').slice(0, 16) : ''
}

async function submitAllocate() {
  if (!allocate.row || allocate.submitting) return
  allocate.submitting = true
  allocate.error = ''
  const berthId = allocate.row.id
  const payload = {
    values: {
      action: '分配靠泊',
      船名: allocate.form.船名.trim(),
      吃水: allocate.form.吃水,
      船长: allocate.form.船长,
      吨位: allocate.form.吨位,
      靠泊开始: toLocalInput(allocate.form.靠泊开始),
      离泊结束: toLocalInput(allocate.form.离泊结束),
    },
    // 每次打开弹窗换新令牌；失败后点「重新提交」沿用同一令牌，保证只按第一次算
    request_id: allocate.requestId,
  }
  try {
    const response = await request(`${ENDPOINT}/${berthId}/actions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    const data = await response.json().catch(() => null)
    if (!response.ok || !data?.ok) {
      // 原样带出服务端报错；弹窗保留已填内容，并给出重试入口
      allocate.error = data?.message || data?.detail || `分配未生效（HTTP ${response.status}）`
      return
    }
    allocate.open = false
    successMessage.value = data.message
    await reload()
  } catch (error) {
    allocate.error = error instanceof Error ? error.message : '分配靠泊请求失败'
  } finally {
    allocate.submitting = false
  }
}

onMounted(reload)
</script>

<style scoped>
.banner {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 8px 12px;
  border-radius: 6px;
  margin-bottom: 10px;
  font-size: 13px;
}
.banner.error { background: #fef3f2; border: 1px solid #fda29b; color: #b42318; }
.banner.success { background: #ecfdf3; border: 1px solid #73e2a3; color: #067647; }
.cell-missing { color: #b54708; background: #fffaeb; padding: 1px 6px; border-radius: 4px; font-size: 12px; }
.window-list { margin: 0; padding-left: 16px; }
.window-list li { font-size: 12px; color: #475467; }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
  width: 520px;
  max-width: calc(100vw - 32px);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.modal h3 { margin: 0; font-size: 15px; }
.modal-hint { margin: 0; font-size: 12px; color: var(--muted); }
.modal label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #344054; }
.modal input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
.form-grid.two-col { grid-template-columns: 1fr 1fr; }
.form-grid label { grid-column: span 1; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
</style>
