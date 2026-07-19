<script setup lang="ts">
import { computed } from 'vue'
import createDOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'

const props = defineProps<{
  content: string
}>()

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: false,
})
const purifier = createDOMPurify(window)

const defaultLinkOpen = markdown.renderer.rules.link_open
markdown.renderer.rules.link_open = (tokens, index, options, environment, renderer) => {
  const token = tokens[index]
  token.attrSet('target', '_blank')
  token.attrSet('rel', 'noopener noreferrer nofollow')
  return defaultLinkOpen
    ? defaultLinkOpen(tokens, index, options, environment, renderer)
    : renderer.renderToken(tokens, index, options)
}

const safeHtml = computed(() => purifier.sanitize(
  markdown.render(props.content ?? ''),
  {
    USE_PROFILES: { html: true },
    ADD_ATTR: ['target'],
    FORBID_TAGS: ['style', 'iframe', 'object', 'embed', 'form'],
    FORBID_ATTR: ['style', 'onerror', 'onload', 'onclick'],
  },
))
</script>

<template>
  <div class="markdown-content" v-html="safeHtml" />
</template>
