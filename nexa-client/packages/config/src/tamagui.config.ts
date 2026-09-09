import { defaultConfig } from '@tamagui/config/v5'
import { createTamagui } from 'tamagui'
import { bodyFont, headingFont } from './fonts'
import { animationsApp } from './animationsApp'
import { nexaDark, nexaLight } from './nexaTheme'

const themes = Object.fromEntries(
  Object.entries(defaultConfig.themes).map(([name, theme]) => {
    const palette = name.startsWith('dark') ? nexaDark : nexaLight
    return [
      name,
      {
        ...theme,
        ...palette,
        ...(name === 'light' || name === 'dark'
          ? { background: palette.nexaCanvas, color: palette.nexaText }
          : {}),
      },
    ]
  })
) as {
  [K in keyof typeof defaultConfig.themes]: (typeof defaultConfig.themes)[K] & typeof nexaLight
}

export const config = createTamagui({
  ...defaultConfig,
  themes,
  animations: animationsApp,
  fonts: {
    body: bodyFont,
    heading: headingFont,
  },
  settings: {
    ...defaultConfig.settings,
    onlyAllowShorthands: false,
  },
})

export type Conf = typeof config

declare module 'tamagui' {
  interface TamaguiCustomConfig extends Conf {}
}
