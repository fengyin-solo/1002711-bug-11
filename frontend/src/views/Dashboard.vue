<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>

    <!-- 泊位占用：与「泊位计划」页共用服务端同一份重算口径，逐项应一致 -->
    <section v-if="berthOccupancy" class="berth-panel">
      <h3>泊位占用总览（随分配明细重算）</h3>
      <div class="stat-row">
        <article v-for="card in berthCards" :key="card.label" class="stat-card">
          <span class="stat-label">{{ card.label }}</span>
          <strong class="stat-value">{{ card.value }}</strong>
        </article>
      </div>
      <table class="data-table">
        <thead>
          <tr><th>泊位编号</th><th>当前靠泊船名</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in berthOccupancy.占用明细" :key="item.泊位编号">
            <td>{{ item.泊位编号 }}</td>
            <td>{{ item.靠泊船名 }}</td>
          </tr>
          <tr v-if="!berthOccupancy.占用明细.length">
            <td colspan="2" class="empty-state">当前无占用泊位</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'

type BerthOccupancy = {
  泊位总数: number
  占用泊位数: number
  空闲泊位数: number
  维护泊位数: number
  不可用泊位数: number
  深水泊位数: number
  普通泊位数: number
  占用率: string
  占用明细: { 泊位编号: string; 靠泊船名: string }[]
}

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
  berth_occupancy?: BerthOccupancy
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])
const berthOccupancy = ref<BerthOccupancy | null>(null)

const berthCards = computed(() => {
  const o = berthOccupancy.value
  if (!o) return []
  return [
    { label: '泊位总数', value: o.泊位总数 },
    { label: '占用泊位', value: o.占用泊位数 },
    { label: '空闲泊位', value: o.空闲泊位数 },
    { label: '维护泊位', value: o.维护泊位数 },
    { label: '深水泊位', value: o.深水泊位数 },
    { label: '占用率', value: o.占用率 },
  ]
})

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
    berthOccupancy.value = payload.berth_occupancy ?? null
  } catch {
    cards.value = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
    moduleRows.value = [{"name": "泊位计划", "created": 0, "pending": 0, "abnormal": 0}, {"name": "船舶作业", "created": 0, "pending": 0, "abnormal": 0}, {"name": "岸桥调度", "created": 0, "pending": 0, "abnormal": 0}, {"name": "堆场策划", "created": 0, "pending": 0, "abnormal": 0}, {"name": "场桥调度", "created": 0, "pending": 0, "abnormal": 0}, {"name": "内集卡调度", "created": 0, "pending": 0, "abnormal": 0}, {"name": "集装箱信息", "created": 0, "pending": 0, "abnormal": 0}, {"name": "闸口管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "危险品申报", "created": 0, "pending": 0, "abnormal": 0}, {"name": "冷藏箱监控", "created": 0, "pending": 0, "abnormal": 0}, {"name": "绑扎加固", "created": 0, "pending": 0, "abnormal": 0}, {"name": "工班管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "箱体修洗", "created": 0, "pending": 0, "abnormal": 0}, {"name": "理货记录", "created": 0, "pending": 0, "abnormal": 0}, {"name": "海关查验", "created": 0, "pending": 0, "abnormal": 0}, {"name": "支线驳船", "created": 0, "pending": 0, "abnormal": 0}, {"name": "超限箱管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "空箱堆存", "created": 0, "pending": 0, "abnormal": 0}, {"name": "能耗监测", "created": 0, "pending": 0, "abnormal": 0}, {"name": "安全巡检", "created": 0, "pending": 0, "abnormal": 0}]
  }
})
</script>

<style scoped>
.berth-panel {
  margin-top: 20px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
}
.berth-panel h3 { margin: 0 0 10px; font-size: 15px; }
</style>
