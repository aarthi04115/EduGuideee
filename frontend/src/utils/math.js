/**
 * Normalizes mathematical formulas inside Markdown text to ensure
 * robust rendering via remark-math and rehype-katex.
 *
 * Converts LaTeX display math delimiters \[ ... \] to $$ ... $$
 * and inline math delimiters \( ... \) to $ ... $.
 */
export function formatMathInMarkdown(content) {
  if (!content || typeof content !== 'string') return ''

  // 1. Normalize \[ ... \] display math delimiters to $$ ... $$
  let normalized = content.replace(/\\\[([\s\S]*?)\\\]/g, (_match, equation) => {
    return `\n\n$$\n${equation.trim()}\n$$\n\n`
  })

  // 2. Normalize \( ... \) inline math delimiters to $ ... $
  normalized = normalized.replace(/\\\(([\s\S]*?)\\\)/g, (_match, equation) => {
    return `$${equation.trim()}$`
  })

  return normalized
}
