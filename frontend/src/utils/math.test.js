import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import katex from 'katex'
import { formatMathInMarkdown } from './math.js'

describe('Math formula normalization and rendering', () => {
  it('normalizes bracket display math \\[ ... \\] to $$ ... $$', () => {
    const input = 'Here is the formula:\n\\[ \\mathbb{E}[X] = \\mu \\]'
    const formatted = formatMathInMarkdown(input)
    assert.match(formatted, /\$\$\n\\mathbb\{E\}\[X\] = \\mu\n\$\$/)
  })

  it('normalizes parenthesis inline math \\( ... \\) to $ ... $', () => {
    const input = 'where \\( x_i \\) and \\( \\hat{y} \\) are the values'
    const formatted = formatMathInMarkdown(input)
    assert.equal(formatted, 'where $x_i$ and $\\hat{y}$ are the values')
  })

  it('renders the complete bias-variance decomposition formula using KaTeX without error', () => {
    const biasVarianceFormula =
      '\\mathbb{E}[(y - \\hat{f}(x))^2] = ' +
      '\\underbrace{\\text{Bias}(\\hat{f}(x))^2}_{\\text{Error from underfitting}} + ' +
      '\\underbrace{\\text{Var}(\\hat{f}(x))}_{\\text{Error from sensitivity to training data}} + ' +
      '\\underbrace{\\sigma^2}_{\\text{Irreducible error}}'

    const html = katex.renderToString(biasVarianceFormula, {
      displayMode: true,
      throwOnError: true,
    })

    assert.ok(html.includes('katex-display'))
    assert.ok(html.includes('underbrace'))
    assert.ok(html.includes('Bias'))
    assert.ok(html.includes('Var'))
  })

  it('renders inline math expressions with Greek letters, hats, and superscripts', () => {
    const expressions = ['\\hat{y}_i', '\\sigma^2', '\\alpha + \\beta', '\\frac{a}{b}']
    for (const expr of expressions) {
      const html = katex.renderToString(expr, {
        displayMode: false,
        throwOnError: true,
      })
      assert.ok(html.includes('katex'))
    }
  })
})
