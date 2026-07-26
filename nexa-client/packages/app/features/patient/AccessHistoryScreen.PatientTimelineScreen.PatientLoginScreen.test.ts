import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function screenSource(name: string): string {
  return readFileSync(
    resolve(process.cwd(), 'packages', 'app', 'features', 'patient', `${name}.tsx`),
    'utf8'
  )
}

describe('patient screen viewport contracts', () => {
  it('keeps Access History virtualized with controls inside the scrolling footer', () => {
    const source = screenSource('AccessHistoryScreen')

    expect(source).toContain('<FlatList')
    expect(source).toContain('flexGrow: history.length === 0 ? 1 : undefined')
    expect(source).toContain('ListFooterComponent')
    expect(source).toContain('Load older records')
    expect(source).toContain('View Health Timeline')
    expect(source).toContain('useFocusEffect')
    expect(source).toContain('response?.data ??')
    expect(source).toContain('No provider has accessed your records yet.')
    expect(source).toContain('paddingBottom: insets.bottom + 32')
    expect(source).toMatch(/<YStack\s+f=\{1\}\s+ai="center"\s+jc="center"/)
  })

  it('keeps the timeline bounded with its footer outside the scroll view', () => {
    const source = screenSource('PatientTimelineScreen')

    expect(source).toMatch(/<ScrollView\s+f=\{1\}/)
    expect(source).toContain('flexGrow: 1')
    expect(source).toContain('paddingBottom: 32')
    expect(source).toMatch(/<YStack\s+f=\{1\}\s+ai="center"\s+jc="center"/)
    expect(source.indexOf('</ScrollView>')).toBeLessThan(source.indexOf('← Access History'))
  })

  it('dismisses the OTP keyboard before navigating from a full-height layout', () => {
    const source = screenSource('PatientLoginScreen')

    expect(source).toContain('KeyboardAvoidingView')
    expect(source).toContain('contentContainerStyle={{ flexGrow: 1 }}')
    expect(source).toContain('keyboardShouldPersistTaps="handled"')
    expect(source.indexOf('Keyboard.dismiss()')).toBeLessThan(
      source.indexOf("router.replace('/patient/access-history')")
    )
  })
})
