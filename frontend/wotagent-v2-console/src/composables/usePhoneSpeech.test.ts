import { describe, expect, it } from 'vitest'
import {
  activityPulseFromBoundary,
  preferredSpeechLang,
  speechText,
} from './usePhoneSpeech'

describe('usePhoneSpeech helpers', () => {
  it('picks Chinese voice when the final answer contains Chinese', () => {
    expect(preferredSpeechLang('张爷爷现在状态稳定，无需通知护理员。')).toBe('zh-CN')
  })

  it('picks English voice when the final answer is English only', () => {
    expect(preferredSpeechLang('Everything looks stable right now.')).toBe('en-US')
  })

  it('strips common markdown syntax before speaking', () => {
    expect(speechText('**结论**：`无需通知`，详情见 [报告](https://example.com)。')).toBe(
      '结论 ：无需通知，详情见 报告。',
    )
  })

  it('creates a stronger activity pulse around punctuation-heavy spoken boundaries', () => {
    const calm = activityPulseFromBoundary('现在状态稳定', 0, 2)
    const emphatic = activityPulseFromBoundary('好的，现在就为您播报。', 3, 5)
    expect(emphatic).toBeGreaterThan(calm)
  })
})
