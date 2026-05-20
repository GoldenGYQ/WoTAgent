<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import VueOfficeDocx from '@vue-office/docx';
import '@vue-office/docx/lib/index.css';
import { api } from '../api/client';

const props = defineProps<{
  open: boolean;
  sessionId?: string | null;
}>();

const emit = defineEmits<{
  (e: 'toggle'): void;
}>();

const currentPath = ref('docs/output.docx');
const recentFiles = ref<string[]>(['docs/output.docx']);
const loading = ref(false);
const syncing = ref(false);
const infoMessage = ref('就绪');
const previewUrl = ref('');
const renderError = ref('');
const renderKey = ref(0);
const sidebarWidth = ref(460);
const resizing = ref(false);
let activeMouseMoveHandler: ((event: MouseEvent) => void) | null = null;
let activeMouseUpHandler: (() => void) | null = null;

const MIN_SIDEBAR_WIDTH = 360;
const MAX_SIDEBAR_WIDTH = 920;
const COLLAPSED_WIDTH = 52;

const sidebarStyle = computed(() => ({
  width: `${props.open ? sidebarWidth.value : COLLAPSED_WIDTH}px`,
}));

const setInfo = (msg: string) => {
  infoMessage.value = msg;
};

const addRecentFile = (path: string) => {
  const normalized = path.trim();
  if (!normalized) return;
  const deduped = [normalized, ...recentFiles.value.filter((item) => item !== normalized)];
  recentFiles.value = deduped.slice(0, 12);
};

const isDocxPath = (path: string) => /\.docx$/i.test(path.trim());

const openDocx = async () => {
  const path = currentPath.value.trim();
  if (!path) {
    setInfo('请先输入文件路径');
    return;
  }
  if (!isDocxPath(path)) {
    setInfo('只读预览仅支持 .docx 文件');
    return;
  }

  loading.value = true;
  renderError.value = '';
  try {
    previewUrl.value = api.getWorkspaceDocxUrl(path, true);
    renderKey.value += 1;
    addRecentFile(path);
    setInfo(`已加载预览 ${path}`);
  } catch (error) {
    renderError.value = String(error);
    setInfo(`加载失败: ${String(error)}`);
  } finally {
    loading.value = false;
  }
};

const refreshDocx = async (source: string = 'manual_refresh') => {
  const path = currentPath.value.trim();
  if (!path || !isDocxPath(path)) return;
  syncing.value = true;
  renderError.value = '';
  try {
    previewUrl.value = api.getWorkspaceDocxUrl(path, true);
    renderKey.value += 1;
    setInfo(`已同步预览 (${source})`);
  } catch (error) {
    renderError.value = String(error);
    setInfo(`同步失败: ${String(error)}`);
  } finally {
    syncing.value = false;
  }
};

const onRendered = () => {
  setInfo(`渲染完成: ${currentPath.value.trim()}`);
};

const onRenderError = (error: unknown) => {
  renderError.value = String(error);
  setInfo(`渲染失败: ${String(error)}`);
};

const applyBackendUpdateFromTool = async (payload: { path: string; source?: string }) => {
  const targetPath = payload.path?.trim();
  if (!targetPath || targetPath !== currentPath.value.trim()) return;
  await refreshDocx(payload.source || 'agent_tool_write');
};

defineExpose({
  applyBackendUpdateFromTool,
});

const pickRecent = (path: string) => {
  currentPath.value = path;
  openDocx();
};

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

const startResize = (event: MouseEvent) => {
  if (!props.open) return;
  event.preventDefault();
  resizing.value = true;
  const startX = event.clientX;
  const startWidth = sidebarWidth.value;

  const onMouseMove = (moveEvent: MouseEvent) => {
    const delta = startX - moveEvent.clientX;
    const nextWidth = clamp(startWidth + delta, MIN_SIDEBAR_WIDTH, MAX_SIDEBAR_WIDTH);
    sidebarWidth.value = nextWidth;
  };

  const onMouseUp = () => {
    resizing.value = false;
    window.removeEventListener('mousemove', onMouseMove);
    window.removeEventListener('mouseup', onMouseUp);
  };

  window.addEventListener('mousemove', onMouseMove);
  window.addEventListener('mouseup', onMouseUp);
  activeMouseMoveHandler = onMouseMove;
  activeMouseUpHandler = onMouseUp;
};

onBeforeUnmount(() => {
  if (activeMouseMoveHandler) {
    window.removeEventListener('mousemove', activeMouseMoveHandler);
    activeMouseMoveHandler = null;
  }
  if (activeMouseUpHandler) {
    window.removeEventListener('mouseup', activeMouseUpHandler);
    activeMouseUpHandler = null;
  }
});
</script>

<template>
  <aside class="doc-sidebar" :class="{ open: props.open, resizing }" :style="sidebarStyle">
    <div v-if="props.open" class="resize-handle" @mousedown="startResize"></div>
    <div class="doc-header">
      <div class="doc-title">
        <h3>文档面板</h3>
        <span class="engine-tag">DOCX 预览</span>
      </div>
      <button class="toggle-btn" @click="emit('toggle')">
        {{ props.open ? '收起' : '展开' }}
      </button>
    </div>

    <template v-if="props.open">
      <div class="resource-bar">
        <input v-model="currentPath" placeholder="DOCX路径，如 docs/output.docx" />
        <button :disabled="loading" @click="openDocx">{{ loading ? '加载中...' : '加载' }}</button>
      </div>

      <div class="quick-actions">
        <button :disabled="syncing" @click="refreshDocx()">{{ syncing ? '同步中...' : '刷新预览' }}</button>
      </div>

      <div class="recent-files">
        <span class="label">最近资源</span>
        <button
          v-for="item in recentFiles"
          :key="item"
          class="recent-item"
          :title="item"
          @click="pickRecent(item)"
        >
          {{ item }}
        </button>
      </div>

      <div class="viewer-wrap">
        <div class="viewer-head">
          <h4>DOCX 只读预览</h4>
          <span class="state">{{ infoMessage }}</span>
        </div>
        <div v-if="!previewUrl" class="empty">请先输入并加载 .docx 文件</div>
        <div v-else class="viewer-box">
          <VueOfficeDocx
            :key="renderKey"
            :src="previewUrl"
            @rendered="onRendered"
            @error="onRenderError"
          />
        </div>
        <div v-if="renderError" class="error-box">
          {{ renderError }}
        </div>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.doc-sidebar {
  border-left: 1px solid #ececf1;
  background: #fafbff;
  transition: width 0.15s ease;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
}

.doc-sidebar.resizing {
  transition: none;
  user-select: none;
}

.resize-handle {
  position: absolute;
  top: 0;
  left: 0;
  width: 8px;
  height: 100%;
  cursor: col-resize;
  z-index: 5;
}

.resize-handle:hover {
  background: rgba(58, 99, 232, 0.08);
}

.doc-header {
  height: 56px;
  border-bottom: 1px solid #ececf1;
  padding: 0 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.doc-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.doc-title h3 {
  margin: 0;
  font-size: 14px;
}

.engine-tag {
  font-size: 11px;
  color: #3a63e8;
  background: #e9efff;
  border-radius: 999px;
  padding: 2px 8px;
}

.toggle-btn {
  border: 1px solid #d8dbe7;
  border-radius: 8px;
  font-size: 12px;
  background: white;
  cursor: pointer;
  padding: 4px 10px;
}

.resource-bar {
  padding: 10px 12px;
  border-bottom: 1px solid #ececf1;
  display: flex;
  gap: 6px;
}

.resource-bar input {
  flex: 1;
  border: 1px solid #d8dbe7;
  border-radius: 8px;
  height: 32px;
  padding: 0 8px;
  font-size: 12px;
}

.resource-bar button,
.quick-actions button {
  border: 1px solid #d8dbe7;
  border-radius: 8px;
  padding: 0 10px;
  background: #fff;
  cursor: pointer;
  font-size: 12px;
  height: 32px;
}

.resource-bar button:disabled,
.quick-actions button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.quick-actions {
  padding: 8px 12px;
  border-bottom: 1px solid #ececf1;
  display: flex;
  gap: 8px;
}

.recent-files {
  padding: 8px 12px;
  border-bottom: 1px solid #ececf1;
  display: flex;
  align-items: center;
  gap: 6px;
  overflow-x: auto;
}

.recent-files .label {
  font-size: 12px;
  color: #666;
  flex-shrink: 0;
}

.recent-item {
  border: 1px solid #dde2f2;
  border-radius: 999px;
  font-size: 11px;
  background: #fff;
  color: #334;
  padding: 2px 8px;
  cursor: pointer;
  max-width: 160px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.viewer-wrap {
  min-height: 520px;
  padding: 10px 12px;
  border-bottom: 1px solid #ececf1;
}

.viewer-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.viewer-head h4 {
  margin: 0;
  font-size: 13px;
}

.state {
  font-size: 11px;
  color: #666;
}

.empty {
  font-size: 12px;
  color: #999;
}

.viewer-box {
  border: 1px solid #e3e6f3;
  border-radius: 10px;
  background: #fff;
  min-height: 440px;
  max-height: 640px;
  overflow: auto;
  padding: 12px;
}

.error-box {
  margin-top: 8px;
  border: 1px solid #ffd2d2;
  background: #ffeded;
  color: #9f2424;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.5;
  padding: 8px 10px;
}
</style>
