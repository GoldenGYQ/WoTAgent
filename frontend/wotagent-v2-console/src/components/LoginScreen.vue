<script setup lang="ts">
import { Eye, HeartHandshake, LogIn, ShieldCheck } from 'lucide-vue-next'
import type { LoginAccount } from '../types/agent'

defineProps<{ accounts: LoginAccount[] }>()
const emit = defineEmits<{ login: [account: LoginAccount] }>()
</script>

<template>
  <main class="login-screen">
    <section class="login-panel">
      <header>
        <span class="login-mark"><HeartHandshake :size="25" /></span>
        <div><h1>WoTAgent 养老协作平台</h1><p>选择实验账号进入工作台</p></div>
      </header>
      <div class="login-account-list">
        <button v-for="account in accounts" :key="account.accountId" type="button" @click="emit('login', account)">
          <span class="account-icon"><ShieldCheck v-if="account.role === 'operator'" :size="21" /><Eye v-else :size="21" /></span>
          <span class="account-copy"><strong>{{ account.accountId }}</strong><small>{{ account.displayName }}</small><em>{{ account.subjectName }} · {{ account.role }}</em></span>
          <LogIn :size="17" />
        </button>
      </div>
      <footer>本地实验账号 · 无密码 · 操作受角色权限约束</footer>
    </section>
  </main>
</template>

<style scoped>
.login-screen { min-height: 100dvh; display: grid; place-items: center; padding: 20px; background: linear-gradient(135deg, #dff6e8 0%, #bceccc 45%, #8ed9a9 100%); }
.login-panel { width: min(100%, 520px); padding: 28px; border: 1px solid var(--line); border-radius: 8px; background: #fff; box-shadow: 0 16px 50px rgba(22, 37, 46, .10); }
header { display: flex; align-items: center; gap: 13px; padding-bottom: 22px; border-bottom: 1px solid var(--line); }.login-mark { width: 46px; height: 46px; display: grid; place-items: center; border-radius: 7px; color: #fff; background: #176b64; }h1 { margin: 0; font-size: 19px; }p { margin: 4px 0 0; color: var(--text-muted); font-size: 12px; }
.login-account-list { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 20px; }.login-account-list button { min-width: 0; min-height: 116px; display: grid; grid-template-columns: 34px 1fr 18px; align-items: center; gap: 8px; padding: 15px; border: 1px solid var(--line-strong); border-radius: 7px; color: var(--text); text-align: left; background: #fff; }.login-account-list button:hover { border-color: #4b918a; background: #f3faf8; }.account-icon { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 50%; color: var(--teal); background: var(--teal-soft); }.account-copy { min-width: 0; display: flex; flex-direction: column; gap: 4px; }.account-copy strong { font-size: 15px; }.account-copy small { color: var(--text); font-size: 11px; }.account-copy em { color: var(--text-muted); font-size: 9px; font-style: normal; overflow-wrap: anywhere; }footer { margin-top: 18px; color: var(--text-faint); text-align: center; font-size: 10px; }
@media (max-width: 520px) { .login-panel { padding: 20px; }.login-account-list { grid-template-columns: 1fr; }.login-account-list button { min-height: 92px; } }
</style>
