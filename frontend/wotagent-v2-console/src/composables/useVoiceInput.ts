import { computed, onBeforeUnmount, ref } from 'vue'
import { agentV2Api } from '../api/v2'

const MIME_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg;codecs=opus',
]

function preferredMimeType(): string {
  if (typeof MediaRecorder === 'undefined') return ''
  return MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type)) ?? ''
}

export function useVoiceInput(
  serverSupported: () => boolean,
  maxSeconds: () => number,
  onTranscript: (text: string) => void,
) {
  const recording = ref(false)
  const transcribing = ref(false)
  const elapsedSeconds = ref(0)
  const error = ref('')
  const browserSupported = (
    typeof navigator !== 'undefined'
    && Boolean(navigator.mediaDevices?.getUserMedia)
    && typeof MediaRecorder !== 'undefined'
  )
  const available = computed(() => browserSupported && serverSupported())
  let recorder: MediaRecorder | null = null
  let stream: MediaStream | null = null
  let chunks: Blob[] = []
  let timer: ReturnType<typeof setInterval> | null = null
  let autoStopTimer: ReturnType<typeof setTimeout> | null = null

  function clearTimers(): void {
    if (timer) clearInterval(timer)
    if (autoStopTimer) clearTimeout(autoStopTimer)
    timer = null
    autoStopTimer = null
  }

  function releaseStream(): void {
    stream?.getTracks().forEach((track) => track.stop())
    stream = null
  }

  async function transcribe(blob: Blob): Promise<void> {
    if (blob.size === 0) {
      error.value = '没有录到有效声音，请重试'
      return
    }
    transcribing.value = true
    try {
      const result = await agentV2Api.transcribeSpeech(blob)
      if (!result.text.trim()) {
        error.value = '没有识别到清晰语音'
        return
      }
      onTranscript(result.text.trim())
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason)
    } finally {
      transcribing.value = false
    }
  }

  function stopRecording(): void {
    if (!recorder || recorder.state === 'inactive') return
    recorder.stop()
  }

  async function startRecording(): Promise<void> {
    if (!available.value || recording.value || transcribing.value) return
    error.value = ''
    elapsedSeconds.value = 0
    chunks = []
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      })
      const mimeType = preferredMimeType()
      recorder = mimeType
        ? new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 32_000 })
        : new MediaRecorder(stream)
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data)
      }
      recorder.onerror = () => {
        error.value = '录音失败，请检查浏览器麦克风权限'
        recording.value = false
        clearTimers()
        releaseStream()
      }
      recorder.onstop = () => {
        const blob = new Blob(chunks, {
          type: recorder?.mimeType || mimeType || 'audio/webm',
        })
        recording.value = false
        clearTimers()
        releaseStream()
        void transcribe(blob)
      }
      recorder.start(250)
      recording.value = true
      timer = setInterval(() => {
        elapsedSeconds.value += 1
      }, 1000)
      autoStopTimer = setTimeout(
        stopRecording,
        Math.max(1, maxSeconds()) * 1000,
      )
    } catch (reason) {
      releaseStream()
      const denied = reason instanceof DOMException && reason.name === 'NotAllowedError'
      error.value = denied
        ? '麦克风权限被拒绝，请在浏览器设置中允许'
        : '无法启动录音，请确认当前页面使用 HTTPS'
    }
  }

  function toggleRecording(): void {
    if (recording.value) stopRecording()
    else void startRecording()
  }

  onBeforeUnmount(() => {
    clearTimers()
    if (recorder?.state !== 'inactive') recorder?.stop()
    releaseStream()
  })

  return {
    available,
    recording,
    transcribing,
    elapsedSeconds,
    error,
    toggleRecording,
  }
}
