<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue';
import { api } from '../api/client';
import type { DeviceInfo, DeviceState } from '../types/api';

const DEVICE_ICONS: Record<string, string> = {
  'light': '💡', 'ac': '❄️', 'tv': '📺', 'purifier': '🌬️',
  'humidifier': '💧', 'fan': '🌀', 'exhaust': '💨',
  'gas_sensor': '⚠️', 'default': '🔌',
};

/** Location code → Chinese display name (partial match) */
function roomDisplayName(loc: string): string {
  const map: Record<string, string> = {
    'living_room': '🛋️ 客厅', 'bedroom': '🛏️ 卧室',
    'bathroom': '🚿 卫生间', 'kitchen': '🍳 厨房',
    'study': '📚 书房', 'balcony': '🌿 阳台', 'hall': '🚪 门厅',
    'garage': '🚗 车库', 'garden': '🌻 花园', 'dining': '🍽️ 餐厅',
  };
  const loc_lower = loc.toLowerCase();
  for (const [key, label] of Object.entries(map)) {
    if (loc_lower.includes(key)) return label;
  }
  return `📍 ${loc}`;
}

/** Sort rooms: known rooms first, then alphabetically */
const ROOM_PRIORITY = ['living_room', 'kitchen', 'bedroom', 'bathroom', 'study', 'hall', 'balcony', 'dining', 'garage', 'garden'];

function sortRooms(rooms: string[]): string[] {
  return [...rooms].sort((a, b) => {
    const ai = ROOM_PRIORITY.findIndex(p => a.toLowerCase().includes(p));
    const bi = ROOM_PRIORITY.findIndex(p => b.toLowerCase().includes(p));
    const pa = ai >= 0 ? ai : 999;
    const pb = bi >= 0 ? bi : 999;
    if (pa !== pb) return pa - pb;
    return a.localeCompare(b);
  });
}

const devices = ref<Record<string, { info: DeviceInfo; state: DeviceState }[]>>({});
const loading = ref(true);

function deviceIcon(devId: string): string {
  for (const [k, v] of Object.entries(DEVICE_ICONS))
    if (devId.startsWith(k)) return v;
  return DEVICE_ICONS.default;
}

function deviceValue(state: DeviceState): { text: string; cls: string } {
  const reallyOn = state.on === true || state.on === 'true' || ((state.brightness ?? 0) > 0);
  if (reallyOn) {
    if (state.currentTemperature != null) return { text: `${state.currentTemperature}°C`, cls: 'temp' };
    if (state.currentHumidity != null) return { text: `${state.currentHumidity}%`, cls: 'humid' };
    if (state.brightness != null) return { text: `${state.brightness}%`, cls: 'on-state' };
    return { text: '🟢 开', cls: 'on-state' };
  }
  if (state.currentTemperature != null) return { text: `${state.currentTemperature}°C`, cls: 'temp' };
  if (state.currentHumidity != null) return { text: `${state.currentHumidity}%`, cls: 'humid' };
  if (state.gasLevel != null) return { text: `燃气 ${state.gasLevel}`, cls: state.gasLevel > 20 ? 'on-state' : 'temp' };
  if (state.pm25 != null) return { text: `PM2.5 ${state.pm25}`, cls: state.pm25 > 80 ? 'temp' : 'on-state' };
  return { text: '⚫ 关', cls: 'off-state' };
}

function isAlert(state: DeviceState): boolean {
  return state.alarm === true || (state.gasLevel != null && state.gasLevel > 30);
}

function getDeviceClass(state: DeviceState): string {
  const reallyOn = state.on === true || state.on === 'true' || ((state.brightness ?? 0) > 0);
  if (isAlert(state)) return 'device alert';
  if (reallyOn) return 'device on';
  return 'device off';
}

async function refreshDevices() {
  try {
    const [devRes, stateRes] = await Promise.all([api.getDevices(), api.getDeviceStates()]);
    const infoMap: Record<string, DeviceInfo> = {};
    for (const d of (devRes.devices || []) as DeviceInfo[]) {
      infoMap[d.id] = d;
    }

    const grouped: Record<string, { info: DeviceInfo; state: DeviceState }[]> = {};
    for (const [devId, state] of Object.entries(stateRes.devices || {})) {
      const info = infoMap[devId];
      if (!info) continue;
      const loc = info.location || 'living_room';
      if (!grouped[loc]) grouped[loc] = [];
      grouped[loc].push({ info, state: state as DeviceState });
    }
    devices.value = grouped;
  } catch (err) {
    console.error('Failed to refresh devices:', err);
  } finally {
    loading.value = false;
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null;

onMounted(() => {
  refreshDevices();
  pollTimer = setInterval(refreshDevices, 5000);
});

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<template>
  <div class="dashboard">
    <div v-if="loading" class="loading">加载中...</div>
    <div v-else class="dashboard-grid">
      <div
        v-for="room in sortRooms(Object.keys(devices))"
        :key="room"
        class="room"
        :class="{ 'room-full': room === 'living_room' || devices[room].length > 4 }"
      >
        <h2>{{ roomDisplayName(room) }}</h2>
        <div class="devices">
          <div
            v-for="item in devices[room]"
            :key="item.info.id"
            :class="getDeviceClass(item.state)"
            :title="item.info.title"
          >
            <div class="icon">{{ deviceIcon(item.info.id) }}</div>
            <div class="name">{{ item.info.title }}</div>
            <div class="value" :class="deviceValue(item.state).cls">
              {{ deviceValue(item.state).text }}
            </div>
          </div>
        </div>
      </div>
      <div v-if="Object.keys(devices).length === 0" class="room room-full">
        <h2>🏠 设备概览</h2>
        <div class="devices">
          <div class="device empty">
            <div class="icon">📭</div>
            <div class="name">无设备</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dashboard {
  padding: 12px;
  overflow-y: auto;
  height: 100%;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  align-content: start;
}

.room {
  background: #1e293b;
  border-radius: 10px;
  padding: 12px;
  border: 1px solid #334155;
}

.room-full {
  grid-column: 1 / -1;
}

.room h2 {
  font-size: 13px;
  font-weight: 500;
  color: #94a3b8;
  margin-bottom: 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid #334155;
}

.devices {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.device {
  background: #0f172a;
  border-radius: 6px;
  padding: 8px 10px;
  min-width: 80px;
  flex: 1 0 auto;
  border: 1px solid #1e293b;
  transition: all 0.3s;
  cursor: default;
  text-align: center;
}

.device.on {
  border-color: #22c55e;
}

.device.off {
  border-color: #334155;
  opacity: 0.6;
}

.device.alert {
  border-color: #ef4444;
  animation: pulse 1.5s infinite;
}

.device.empty {
  opacity: 0.3;
}

.icon {
  font-size: 18px;
  margin-bottom: 2px;
}

.name {
  font-size: 11px;
  color: #64748b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.value {
  font-size: 14px;
  font-weight: 600;
  margin-top: 1px;
}

.value.temp { color: #f97316; }
.value.humid { color: #38bdf8; }
.value.on-state { color: #22c55e; }
.value.off-state { color: #475569; }

.loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #64748b;
  font-size: 14px;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
</style>
