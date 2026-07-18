import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { agentV2Api } from '../api/v2'
import { useVoiceInput } from './useVoiceInput'

class FakeMediaRecorder {
  static isTypeSupported = vi.fn(() => true)
  state: RecordingState = 'inactive'
  mimeType = 'audio/webm;codecs=opus'
  ondataavailable: ((event: BlobEvent) => void) | null = null
  onerror: (() => void) | null = null
  onstop: (() => void) | null = null

  constructor(_stream: MediaStream, _options?: MediaRecorderOptions) {}

  start(): void {
    this.state = 'recording'
  }

  stop(): void {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['voice'], { type: 'audio/webm' }) } as BlobEvent)
    this.onstop?.()
  }
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('useVoiceInput', () => {
  it('records, uploads, and returns mixed-language transcript text', async () => {
    const stopTrack = vi.fn()
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: stopTrack }],
        }),
      },
    })
    vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
    vi.spyOn(agentV2Api, 'transcribeSpeech').mockResolvedValue({
      text: '打开 living room 的灯',
      language: 'zh',
      language_probability: 0.96,
      duration_seconds: 1.8,
      processing_ms: 42,
      model: 'base',
    })
    const onTranscript = vi.fn()
    let voice!: ReturnType<typeof useVoiceInput>
    const wrapper = mount(defineComponent({
      setup() {
        voice = useVoiceInput(() => true, () => 30, onTranscript)
        return () => null
      },
    }))

    voice.toggleRecording()
    await flushPromises()
    expect(voice.recording.value).toBe(true)

    voice.toggleRecording()
    await flushPromises()

    expect(agentV2Api.transcribeSpeech).toHaveBeenCalledOnce()
    expect(onTranscript).toHaveBeenCalledWith('打开 living room 的灯')
    expect(stopTrack).toHaveBeenCalledOnce()
    expect(voice.recording.value).toBe(false)
    expect(voice.transcribing.value).toBe(false)
    wrapper.unmount()
  })
})
