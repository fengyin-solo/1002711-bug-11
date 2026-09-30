<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
    </header>

    <h3 class="section-title">泊位总览</h3>
    <p v-if="berthError" class="inline-error" role="alert">
      {{ berthError }}
      <button class="link" type="button" @click="loadBerthOverview">重试</button>
    </p>
    <template v-else>
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">泊位总数</span>
          <strong class="stat-value">{{ berthOverview?.总数 ?? '-' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">占用泊位</span>
          <strong class="stat-value">{{ berthOverview?.占用数 ?? '-' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">空闲泊位</span>
          <strong class="stat-value">{{ berthOverview?.空闲数 ?? '-' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">维护泊位</span>
          <strong class="stat-value">{{ berthOverview?.维护数 ?? '-' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">泊位占用率</span>
          <strong class="stat-value">{{ berthOverview ? `${berthOverview.占用率}%` : '-' }}</strong>
        </article>
      </div>
      <table class="data-table berth-table">
        <thead>
          <tr><th>泊位编号</th><th>靠泊船名</th><th>靠泊时段</th><th>离泊时段</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in berthOverview?.占用明细 ?? []" :key="item.泊位编号">
            <td>{{ item.泊位编号 }}</td>
            <td>{{ item.靠泊船名 }}</td>
            <td>{{ item.靠泊时段 }}</td>
            <td>{{ item.离泊时段 }}</td>
          </tr>
          <tr v-if="berthOverview && berthOverview.占用明细.length === 0">
            <td colspan="4" class="empty-state">当前没有生效中的排靠记录</td>
          </tr>
        </tbody>
      </table>
      <p class="section-note">占用数随泊位分配明细实时重算，与泊位计划页为同一份数据口径。</p>
    </template>

    <h3 class="section-title">各模块待处理</h3>
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
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { extractError, fetchJson, request } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

type BerthOverview = {
  总数: number
  占用数: number
  空闲数: number
  维护数: number
  不可用数: number
  占用率: number
  占用明细: { 泊位编号: string; 靠泊船名: string; 靠泊时段: string; 离泊时段: string }[]
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])
const berthOverview = ref<BerthOverview | null>(null)
const berthError = ref('')

async function loadBerthOverview() {
  berthError.value = ''
  try {
    const response = await request('/api/berth/overview')
    if (!response.ok) {
      throw new Error(await extractError(response))
    }
    berthOverview.value = (await response.json()) as BerthOverview
  } catch (error) {
    berthError.value = error instanceof Error ? error.message : '泊位总览读取失败'
  }
}

onMounted(async () => {
  void loadBerthOverview()
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
  } catch {
    cards.value = [{ label: '业务模块', value: 0 }, { label: '今日新增', value: 0 }]
    moduleRows.value = []
  }
})
</script>
