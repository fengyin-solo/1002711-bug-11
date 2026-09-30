<template>
  <section class="page" data-module="berth">
    <header class="page-head">
      <div>
        <h2>泊位计划管理</h2>
        <p class="page-desc">围绕泊位编号、泊位长度、水深条件、可停吨位做登记、排靠泊与释放；占用数以分配明细实时重算。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记泊位</button>
        <button class="btn" type="button" @click="exportRows">导出泊位计划清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
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
          <option value="">全部状态</option>
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <p v-if="listError" class="inline-error" role="alert">
      {{ listError }}
      <button class="link" type="button" @click="reload">重试</button>
    </p>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>资料状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <span v-if="isMissing(row, column)" class="missing-tag">待补充（缺：{{ column }}）</span>
            <template v-else>{{ displayValue(row, column) }}</template>
          </td>
          <td>
            <span v-if="missingFields(row).length" class="missing-tag">
              待补充：{{ missingFields(row).join('、') }}
            </span>
            <span v-else class="ok-tag">资料齐全</span>
          </td>
          <td class="row-actions">
            <button
              class="link"
              type="button"
              :disabled="busyId === String(row.id)"
              @click="openAllocate(row)"
            >
              分配靠泊
            </button>
            <button
              class="link"
              type="button"
              :disabled="busyId === String(row.id)"
              @click="runSimpleAction('释放泊位', row)"
            >
              释放泊位
            </button>
            <button
              class="link"
              type="button"
              :disabled="busyId === String(row.id)"
              @click="runSimpleAction('登记维护', row)"
            >
              登记维护
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无泊位计划数据，可先登记泊位</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条泊位计划记录</span>
      <span v-if="actionMessage" :class="actionOk ? 'ok-text' : 'error-text'">
        {{ actionMessage }}
        <button v-if="!actionOk && lastAction" class="link" type="button" @click="retryLastAction">重试</button>
      </span>
    </footer>

    <!-- 排靠泊弹窗：跨天靠离泊直接按填写时间提交，结束早于开始时由服务端顺延到次日 -->
    <div v-if="allocateOpen" class="modal-mask" @click.self="closeAllocate">
      <div class="modal">
        <h3 class="modal-title">分配靠泊 · {{ allocateForm.berthNo }}</h3>
        <p class="modal-hint">
          泊位水深 {{ allocateForm.berthDepth }} 米 / 长度 {{ allocateForm.berthLength }} 米 /
          可停吨位 {{ allocateForm.berthTonnage }} 吨；水深条件不足会被拦下。
          靠泊时段跨到第二天时，离泊时段直接填次日时间即可。
        </p>
        <div class="form-grid">
          <label v-for="field in allocateFieldDefs" :key="field.name" class="form-item">
            <span>{{ field.label }}</span>
            <input
              v-model="allocateForm.values[field.name]"
              :type="field.type ?? 'text'"
              :placeholder="field.placeholder"
            />
          </label>
        </div>
        <p v-if="allocateError" class="inline-error" role="alert">{{ allocateError }}</p>
        <div class="modal-actions">
          <button class="btn" type="button" :disabled="submitting" @click="closeAllocate">取消</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="submitAllocate">
            {{ submitting ? '提交中…' : '确认排靠' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 登记泊位弹窗 -->
    <div v-if="createOpen" class="modal-mask" @click.self="closeCreate">
      <div class="modal">
        <h3 class="modal-title">登记泊位</h3>
        <div class="form-grid">
          <label v-for="field in createFieldDefs" :key="field" class="form-item">
            <span>{{ field }}</span>
            <input v-model="createForm[field]" :placeholder="`请输入${field}`" />
          </label>
        </div>
        <p v-if="createError" class="inline-error" role="alert">{{ createError }}</p>
        <div class="modal-actions">
          <button class="btn" type="button" :disabled="creating" @click="closeCreate">取消</button>
          <button class="btn primary" type="button" :disabled="creating" @click="submitCreate">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { extractError, request } from '@/api/client'

type Scalar = string | number | boolean | null
type Row = {
  id: number | string
  status?: string
  待补充?: string[]
  allocations?: Array<Record<string, Scalar>>
  [key: string]: Scalar | string[] | Array<Record<string, Scalar>> | undefined
}

type Overview = {
  总数: number
  占用数: number
  空闲数: number
  维护数: number
  不可用数: number
  占用率: number
}

const ENDPOINT = '/api/berth'
const columns = ['泊位编号', '泊位长度', '水深条件', '可停吨位', '靠泊时段', '离泊时段', '靠泊船名', '泊位状态']
const statuses = ['空闲', '已靠泊', '维护中', '不可用']

const rows = ref<Row[]>([])
const total = ref(0)
const listError = ref('')
const keyword = ref('')
const statusFilter = ref('')

const stats = ref<{ label: string; value: number | string }[]>([
  { label: '泊位总数', value: '-' },
  { label: '占用泊位', value: '-' },
  { label: '空闲泊位', value: '-' },
  { label: '维护泊位', value: '-' },
  { label: '泊位占用率', value: '-' },
])

// 动作反馈与重试
const actionMessage = ref('')
const actionOk = ref(false)
const busyId = ref('')
const lastAction = ref<{ action: string; id: number | string; values?: Record<string, string> } | null>(null)

// 排靠泊弹窗
const allocateFieldDefs: { name: string; label: string; type?: string; placeholder: string }[] = [
  { name: '靠泊船名', label: '靠泊船名', placeholder: '如：远东快轮' },
  { name: '船舶吃水', label: '船舶吃水（米）', placeholder: '如：15.2' },
  { name: '船舶长度', label: '船舶长度（米）', placeholder: '如：304' },
  { name: '船舶总吨', label: '船舶总吨（吨）', placeholder: '如：152000' },
  { name: '靠泊时段', label: '靠泊时段', type: 'datetime-local', placeholder: 'YYYY-MM-DD HH:mm' },
  { name: '离泊时段', label: '离泊时段（可跨次日）', type: 'datetime-local', placeholder: 'YYYY-MM-DD HH:mm' },
]

const allocateOpen = ref(false)
const submitting = ref(false)
const allocateError = ref('')
const allocateForm = reactive({
  entryId: '' as string | number,
  berthNo: '',
  berthDepth: '',
  berthLength: '',
  berthTonnage: '',
  values: {
    靠泊船名: '',
    船舶吃水: '',
    船舶长度: '',
    船舶总吨: '',
    靠泊时段: '',
    离泊时段: '',
  } as Record<string, string>,
})

// 登记泊位弹窗
const createFieldDefs = ['泊位编号', '泊位长度', '水深条件', '可停吨位'] as const
const createOpen = ref(false)
const creating = ref(false)
const createError = ref('')
const createForm = reactive<Record<string, string>>({
  泊位编号: '',
  泊位长度: '',
  水深条件: '',
  可停吨位: '',
})

function isMissing(row: Row, column: string): boolean {
  return Array.isArray(row.待补充) && row.待补充.includes(column)
}

function missingFields(row: Row): string[] {
  return Array.isArray(row.待补充) ? row.待补充 : []
}

function displayValue(row: Row, column: string): string {
  const value = row[column]
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

// ---------------------------------------------------------------- 排靠泊弹窗
function openAllocate(row: Row) {
  allocateForm.entryId = row.id
  allocateForm.berthNo = String(row['泊位编号'] ?? '')
  allocateForm.berthDepth = displayValue(row, '水深条件')
  allocateForm.berthLength = displayValue(row, '泊位长度')
  allocateForm.berthTonnage = displayValue(row, '可停吨位')
  allocateFieldDefs.forEach((field) => {
    allocateForm.values[field.name] = ''
  })
  allocateError.value = ''
  allocateOpen.value = true
}

function closeAllocate() {
  if (submitting.value) {
    return
  }
  allocateOpen.value = false
}

async function submitAllocate() {
  allocateError.value = ''
  submitting.value = true
  try {
    // datetime-local 的值形如 2026-10-01T06:00，换成空格分隔即可；服务端两种都认
    const values: Record<string, string> = {}
    allocateFieldDefs.forEach((field) => {
      values[field.name] = allocateForm.values[field.name].replace('T', ' ')
    })
    const result = await postAction(allocateForm.entryId, '分配靠泊', values)
    if (result.ok) {
      allocateOpen.value = false
      setActionMessage(true, result.message)
      await Promise.all([reload(), reloadOverview()])
    } else {
      // 失败不关弹窗、不清表单：服务端报错原样带出，用户改完直接重试
      allocateError.value = result.message
      lastAction.value = { action: '分配靠泊', id: allocateForm.entryId, values }
    }
  } finally {
    submitting.value = false
  }
}

// ---------------------------------------------------------------- 登记泊位
function openCreate() {
  createFieldDefs.forEach((field) => {
    createForm[field] = ''
  })
  createError.value = ''
  createOpen.value = true
}

function closeCreate() {
  if (creating.value) {
    return
  }
  createOpen.value = false
}

async function submitCreate() {
  createError.value = ''
  creating.value = true
  try {
    const values: Record<string, string> = {}
    createFieldDefs.forEach((field) => {
      values[field] = createForm[field]
    })
    const response = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values }) })
    const payload = (await response.json()) as { ok: boolean; message: string }
    if (!response.ok || !payload.ok) {
      createError.value = payload.message || (await extractError(response))
      return
    }
    createOpen.value = false
    setActionMessage(true, payload.message)
    await Promise.all([reload(), reloadOverview()])
  } catch (error) {
    createError.value = error instanceof Error ? error.message : '泊位登记失败'
  } finally {
    creating.value = false
  }
}

// ---------------------------------------------------------------- 动作提交
async function runSimpleAction(action: string, row: Row) {
  busyId.value = String(row.id)
  try {
    const result = await postAction(row.id, action)
    setActionMessage(result.ok, result.message)
    if (result.ok) {
      await Promise.all([reload(), reloadOverview()])
    } else {
      lastAction.value = { action, id: row.id }
    }
  } finally {
    busyId.value = ''
  }
}

async function retryLastAction() {
  if (!lastAction.value) {
    return
  }
  const { action, id, values } = lastAction.value
  busyId.value = String(id)
  try {
    const result = await postAction(id, action, values ?? {})
    setActionMessage(result.ok, result.message)
    if (result.ok) {
      lastAction.value = null
      allocateOpen.value = false
      await Promise.all([reload(), reloadOverview()])
    }
  } finally {
    busyId.value = ''
  }
}

async function postAction(
  id: string | number,
  action: string,
  values: Record<string, string> = {},
): Promise<{ ok: boolean; message: string }> {
  try {
    const response = await request(`${ENDPOINT}/${id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, ...values } }),
    })
    if (!response.ok) {
      return { ok: false, message: await extractError(response) }
    }
    const payload = (await response.json()) as { ok: boolean; message: string }
    return { ok: Boolean(payload.ok), message: payload.message || '操作未返回说明' }
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : '泊位计划操作失败' }
  }
}

function setActionMessage(ok: boolean, message: string) {
  actionOk.value = ok
  actionMessage.value = message
}

// ---------------------------------------------------------------- 列表与总览
async function reload() {
  listError.value = ''
  const query = new URLSearchParams()
  if (keyword.value.trim()) {
    query.set('keyword', keyword.value.trim())
  }
  if (statusFilter.value) {
    query.set('status', statusFilter.value)
  }
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error(await extractError(response))
    }
    const payload = (await response.json()) as { items?: Row[]; total?: number }
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    // 读列表失败只在错误条里提示并给重试，不清空已渲染的行、不卡住页面
    listError.value = error instanceof Error ? error.message : '泊位列表读取失败'
  }
}

async function reloadOverview() {
  try {
    const response = await request(`${ENDPOINT}/overview`)
    if (!response.ok) {
      return
    }
    const data = (await response.json()) as Overview
    stats.value = [
      { label: '泊位总数', value: data.总数 },
      { label: '占用泊位', value: data.占用数 },
      { label: '空闲泊位', value: data.空闲数 },
      { label: '维护泊位', value: data.维护数 },
      { label: '泊位占用率', value: `${data.占用率}%` },
    ]
  } catch {
    // 总览失败不拖垮列表，卡片保持上一次的值
  }
}

onMounted(() => {
  void Promise.all([reload(), reloadOverview()])
})
</script>
