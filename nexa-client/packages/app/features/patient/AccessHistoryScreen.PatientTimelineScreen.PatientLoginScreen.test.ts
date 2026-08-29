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
  it('keeps Access History virtualized with a stable Timeline action', () => {
    const source = screenSource('AccessHistoryScreen')

    expect(source).toContain('<FlatList')
    expect(source).toContain('flexGrow: history.length === 0 ? 1 : 0')
    expect(source).toContain('ListFooterComponent')
    expect(source).toContain('Load older records')
    expect(source).toContain('View Health Timeline')
    expect(source).toContain('useFocusEffect')
    expect(source).toContain('normalizeAccessHistoryResponse')
    expect(source).not.toContain('AH-FIX-02')
    expect(source).not.toContain('Access record')
    expect(source).toContain('data={history}')
    expect(source).toContain('<View style={{ flex: 1 }}>')
    expect(source).toContain('collapsable={false}')
    expect(source).toContain('windowSize={5}')
    expect(source).toContain('flexShrink={0}')
    expect(source).toContain('No provider has accessed your records yet.')
    expect(source).toContain('paddingBottom: insets.bottom + 24')
    expect(source).toMatch(/<YStack\s+flex=\{1\}\s+alignItems="center"\s+justifyContent="center"/)
  })

  it('uses a native SectionList with an unobstructed stable action', () => {
    const source = screenSource('PatientTimelineScreen')

    expect(source).toContain('<SectionList')
    expect(source).not.toContain('<ScrollView')
    expect(source).toContain('flexGrow: sections.length === 0 ? 1 : 0')
    expect(source).toContain('paddingBottom: insets.bottom + 96')
    expect(source).toContain('RefreshControl')
    expect(source).toMatch(/<YStack\s+flex=\{1\}\s+alignItems="center"\s+justifyContent="center"/)
    expect(source.indexOf('<SectionList')).toBeLessThan(source.indexOf('Access History'))
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
