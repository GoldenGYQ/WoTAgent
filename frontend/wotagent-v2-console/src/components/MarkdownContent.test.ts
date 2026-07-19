// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import MarkdownContent from './MarkdownContent.vue'

describe('MarkdownContent', () => {
  it('renders structured Markdown used by Agent responses', () => {
    const wrapper = mount(MarkdownContent, {
      props: {
        content: [
          '## 设备结果',
          '',
          '- **客厅灯**：开启',
          '- 亮度：45%',
          '',
          '| 设备 | 状态 |',
          '| --- | --- |',
          '| 空调 | 关闭 |',
          '',
          '`verified=true`',
        ].join('\n'),
      },
    })

    expect(wrapper.find('h2').text()).toBe('设备结果')
    expect(wrapper.findAll('li')).toHaveLength(2)
    expect(wrapper.find('strong').text()).toBe('客厅灯')
    expect(wrapper.find('table').exists()).toBe(true)
    expect(wrapper.find('code').text()).toBe('verified=true')
  })

  it('escapes raw HTML and sanitizes unsafe links', () => {
    const wrapper = mount(MarkdownContent, {
      props: {
        content: [
          '<script>alert("xss")</script>',
          '',
          '[危险链接](javascript:alert(1))',
          '',
          '[项目文档](https://example.com/docs)',
        ].join('\n'),
      },
    })

    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.html()).not.toContain('href="javascript:')
    const safeLink = wrapper.find('a[href="https://example.com/docs"]')
    expect(safeLink.attributes('target')).toBe('_blank')
    expect(safeLink.attributes('rel')).toBe('noopener noreferrer nofollow')
  })
})
