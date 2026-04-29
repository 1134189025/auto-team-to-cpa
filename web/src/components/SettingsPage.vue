<template>
  <section class="rounded-3xl border border-slate-800 bg-slate-950/70 p-6">
    <div class="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
      <div>
        <h2 class="text-xl font-semibold text-white">设置</h2>
        <p class="mt-1 text-sm text-slate-400">在这里修改 Freemail、CPA、代理和面板 API Key，不用再手动改 `.env`。</p>
      </div>
      <button
        @click="load"
        :disabled="loading"
        class="rounded-2xl border border-slate-700 px-4 py-2 text-sm text-slate-200 transition hover:border-cyan-500 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
      >
        {{ loading ? '读取中...' : '重新读取' }}
      </button>
    </div>

    <div v-if="message" class="mt-4 rounded-2xl border px-4 py-3 text-sm" :class="messageClass">
      {{ message }}
    </div>

    <div class="mt-6 grid gap-4 md:grid-cols-2">
      <div v-for="field in fields" :key="field.key" class="rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
        <label class="block text-sm text-slate-300">
          {{ field.prompt }}
          <span v-if="!field.optional" class="text-rose-400">*</span>
          <span v-if="field.key === 'API_KEY'" class="ml-1 text-xs text-slate-500">留空自动生成新 Key</span>
        </label>
        <input
          v-model="form[field.key]"
          :type="inputType(field.key)"
          :min="field.key === 'TARGET_CHILDREN_PER_PARENT' ? 1 : undefined"
          :max="field.key === 'TARGET_CHILDREN_PER_PARENT' ? 50 : undefined"
          :placeholder="field.default || ''"
          class="mt-2 w-full rounded-2xl border border-slate-800 bg-slate-950 px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-500"
        />
        <div class="mt-2 text-xs" :class="field.configured ? 'text-emerald-300' : 'text-amber-300'">
          {{ field.configured ? '当前已配置' : '当前未配置' }}
        </div>
      </div>
    </div>

    <div class="mt-6 flex flex-wrap gap-3">
      <button
        @click="save"
        :disabled="saving"
        class="rounded-2xl bg-cyan-500 px-4 py-3 text-sm font-medium text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {{ saving ? '保存中...' : '保存设置' }}
      </button>
    </div>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api, setApiKey } from '../api.js'

const emit = defineEmits(['saved'])

const fields = ref([])
const form = reactive({})
const loading = ref(false)
const saving = ref(false)
const message = ref('')
const messageClass = ref('')

function inputType(key) {
  if (key === 'TARGET_CHILDREN_PER_PARENT') return 'number'
  return key.includes('PASSWORD') || key.includes('KEY') || key.includes('TOKEN') ? 'password' : 'text'
}

async function load() {
  loading.value = true
  message.value = ''
  try {
    const result = await api.getSettings()
    fields.value = result.fields
    for (const field of result.fields) {
      form[field.key] = field.value ?? field.default ?? ''
    }
  } catch (error) {
    message.value = error.message || '读取设置失败'
    messageClass.value = 'border-rose-500/20 bg-rose-500/10 text-rose-200'
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  message.value = ''
  try {
    const result = await api.saveSettings({ ...form })
    if (result.api_key) {
      setApiKey(result.api_key)
      form.API_KEY = result.api_key
    }
    message.value = result.message || '设置已保存'
    messageClass.value = 'border-emerald-500/20 bg-emerald-500/10 text-emerald-200'
    await load()
    emit('saved')
  } catch (error) {
    message.value = error.message || '保存设置失败'
    messageClass.value = 'border-rose-500/20 bg-rose-500/10 text-rose-200'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
