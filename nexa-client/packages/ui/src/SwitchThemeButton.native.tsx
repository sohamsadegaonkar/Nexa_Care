'use client'

import { useState } from 'react'
import { Appearance, useColorScheme } from 'react-native'
import { Button, useIsomorphicLayoutEffect } from 'tamagui'

export const SwitchThemeButton = () => {
  const colorScheme = useColorScheme()
  const [clientTheme, setClientTheme] = useState<string>('light')

  useIsomorphicLayoutEffect(() => {
    setClientTheme(colorScheme || 'light')
  }, [colorScheme])

  const nextTheme = clientTheme === 'dark' ? 'light' : 'dark'

  return (
    <Button
      accessibilityLabel={`Change theme: ${clientTheme}`}
      onPress={() => Appearance.setColorScheme(nextTheme)}
    >
      {clientTheme === 'dark' ? 'Light appearance' : 'Dark appearance'}
    </Button>
  )
}
