<script setup>
import { useRouter } from 'vue-router'
const router = useRouter()
</script>

<template>
  <div class="notfound-page">
    <div class="notfound-inner">
      <div class="notfound-code" aria-hidden="true">404</div>
      <h1 class="notfound-title">Page not found</h1>
      <p class="notfound-desc">This route doesn't exist in hal0's dashboard.</p>
      <button class="btn-primary" type="button" @click="router.push('/')">
        ← Back to dashboard
      </button>
    </div>
  </div>
</template>

<style scoped>
.notfound-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: 32px;
}

.notfound-inner {
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.notfound-code {
  font-family: var(--font-mono);
  font-size: 72px;
  font-weight: 600;
  color: var(--color-fg-faint);
  line-height: 1;
  letter-spacing: -4px;
}

.notfound-title {
  font-size: 22px;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0;
}

.notfound-desc {
  font-size: 14px;
  color: var(--color-fg-muted);
  margin: 0;
}

.btn-primary {
  margin-top: 8px;
  padding: 8px 20px;
  border-radius: var(--radius);
  background: var(--color-accent);
  color: var(--color-bg);
  font-size: 14px;
  font-weight: 600;
  border: none;
  cursor: pointer;
}
.btn-primary:hover { opacity: 0.88; }
</style>
