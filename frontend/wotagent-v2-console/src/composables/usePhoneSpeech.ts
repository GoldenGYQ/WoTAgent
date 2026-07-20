import { computed, ref } from 'vue'

const PHONE_MODE_KEY = 'wotagent-v2-console.phone-mode'
const CJK_PATTERN = /[\u3400-\u9fff]/
const PHONE_WAVE_BARS = 12

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export function speechText(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[#>*_\-]{1,3}/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

export function preferredSpeechLang(text: string): 'zh-CN' | 'en-US' {
  return CJK_PATTERN.test(text) ? 'zh-CN' : 'en-US'
}

export function activityPulseFromBoundary(
  text: string,
  charIndex: number,
  charLength = 0,
): number {
  const start = Math.max(0, charIndex)
  const size = Math.max(charLength, 4)
  const segment = text.slice(start, start + size).trim() || text.slice(start, start + 1)
  const compact = segment.replace(/\s+/g, '')
  let pulse = 0.28 + Math.min(compact.length, 10) * 0.045
  if (/[,.!?，。！？；：:]/.test(segment)) pulse += 0.18
  if (CJK_PATTERN.test(segment)) pulse += 0.06
  return clamp(pulse, 0.24, 0.95)
}

function restingWaveform(): number[] {
  return Array.from({ length: PHONE_WAVE_BARS }, (_, index) => {
    const distance = Math.abs(index - (PHONE_WAVE_BARS - 1) / 2)
    const centerWeight = 1 - distance / ((PHONE_WAVE_BARS - 1) / 2)
    return Number((0.18 + centerWeight * 0.08).toFixed(3))
  })
}

function preferredVoice(
  voices: SpeechSynthesisVoice[],
  text: string,
): SpeechSynthesisVoice | null {
  const target = preferredSpeechLang(text).toLowerCase().slice(0, 2)
  return voices.find((voice) => voice.lang.toLowerCase().startsWith(target)) ?? voices[0] ?? null
}

export function usePhoneSpeech() {
  const supported = typeof window !== 'undefined'
    && 'speechSynthesis' in window
    && 'SpeechSynthesisUtterance' in window
  const enabled = ref(
    supported && localStorage.getItem(PHONE_MODE_KEY) === 'true',
  )
  const speaking = ref(false)
  const activityLevel = ref(0.18)
  const waveform = ref<number[]>(restingWaveform())
  const spokenRunIds = new Set<string>()
  let frameId = 0
  let activityTarget = 0.18
  let phase = 0

  function cancel(): void {
    if (!supported) return
    window.speechSynthesis.cancel()
    speaking.value = false
    stopWaveform()
  }

  function setEnabled(value: boolean): void {
    enabled.value = value
    localStorage.setItem(PHONE_MODE_KEY, String(value))
    if (!value) cancel()
  }

  function tickWaveform(timestamp: number): void {
    if (!speaking.value) {
      frameId = 0
      return
    }
    activityTarget = Math.max(0.18, activityTarget * 0.92)
    activityLevel.value = clamp(
      activityLevel.value * 0.68 + activityTarget * 0.32,
      0.18,
      1,
    )
    phase += 0.16 + activityLevel.value * 0.18
    waveform.value = Array.from({ length: PHONE_WAVE_BARS }, (_, index) => {
      const center = 1 - Math.abs(index - (PHONE_WAVE_BARS - 1) / 2) / ((PHONE_WAVE_BARS - 1) / 2)
      const wave = (Math.sin(phase + index * 0.78) + 1) / 2
      const shimmer = (Math.sin(timestamp / 180 + index * 1.13) + 1) / 2
      return Number(clamp(
        0.18 + center * 0.1 + activityLevel.value * 0.42 * wave + 0.08 * shimmer,
        0.14,
        1,
      ).toFixed(3))
    })
    frameId = window.requestAnimationFrame(tickWaveform)
  }

  function startWaveform(seed = 0.42): void {
    if (!supported) return
    activityTarget = Math.max(activityTarget, seed)
    if (frameId) return
    frameId = window.requestAnimationFrame(tickWaveform)
  }

  function stopWaveform(): void {
    if (!supported) return
    if (frameId) window.cancelAnimationFrame(frameId)
    frameId = 0
    activityTarget = 0.18
    activityLevel.value = 0.18
    waveform.value = restingWaveform()
  }

  function speakFinal(runId: string, text: string): void {
    if (!supported || !enabled.value || spokenRunIds.has(runId)) return
    const content = speechText(text)
    if (!content) return
    cancel()
    const utterance = new SpeechSynthesisUtterance(content)
    utterance.lang = preferredSpeechLang(content)
    utterance.rate = utterance.lang.startsWith('zh') ? 1 : 1.02
    utterance.pitch = 1.03
    const voice = preferredVoice(window.speechSynthesis.getVoices(), content)
    if (voice) utterance.voice = voice
    utterance.onstart = () => {
      speaking.value = true
      startWaveform(0.48)
    }
    utterance.onboundary = (event) => {
      activityTarget = Math.max(
        activityTarget,
        activityPulseFromBoundary(content, event.charIndex, event.charLength ?? 0),
      )
      startWaveform(activityTarget)
    }
    const onDone = () => {
      speaking.value = false
      stopWaveform()
    }
    utterance.onend = onDone
    utterance.onerror = onDone
    spokenRunIds.add(runId)
    window.speechSynthesis.speak(utterance)
  }

  return {
    enabled,
    supported: computed(() => supported),
    speaking,
    activityLevel,
    waveform,
    statusLabel: computed(() => (
      speaking.value
        ? '正在播报最终回复'
        : '待命中，仅在最终回复时播报'
    )),
    setEnabled,
    cancel,
    speakFinal,
  }
}
