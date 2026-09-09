'use client'

import { useState } from 'react'
import { useIsomorphicLayoutEffect } from 'tamagui'
import { ActionButton } from './NexaPrimitives'
import { useThemeSetting, useRootTheme } from '@tamagui/next-theme'

export const SwitchThemeButton = () => {
  const themeSetting = useThemeSetting()
  const [theme] = useRootTheme()

  const [clientTheme, setClientTheme] = useState<string | undefined>('light')

  useIsomorphicLayoutEffect(() => {
    setClientTheme(themeSetting.forcedTheme || themeSetting.resolvedTheme || theme)
  }, [themeSetting.current, themeSetting.resolvedTheme])

  return (
    <ActionButton
      aria-label={`Change theme: ${clientTheme}`}
      onPress={() => themeSetting.set(clientTheme === 'dark' ? 'light' : 'dark')}
    >
      {clientTheme === 'dark' ? 'Light appearance' : 'Dark appearance'}
    </ActionButton>
  )
}
