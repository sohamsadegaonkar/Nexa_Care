import type React from 'react'
import { render } from '@testing-library/react'
import { Provider } from '../packages/app/provider'

export function renderWithTamagui(ui: React.ReactElement) {
  return render(<Provider defaultTheme="light">{ui}</Provider>)
}
