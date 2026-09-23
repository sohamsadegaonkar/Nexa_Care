import { describe, expect, it, vi } from 'vitest'
import { screen, fireEvent, render } from '@testing-library/react'
import { Provider } from 'app/provider'
import { ActionButton } from '@my/ui'
import { useState } from 'react'

function renderButton(ui: React.ReactElement) {
  return render(ui, {
    wrapper: ({ children }) => <Provider defaultTheme="light">{children}</Provider>,
  })
}

describe('ActionButton component regression & render stability', () => {
  it('renders correctly with default, primary, and danger intents', () => {
    const { rerender } = renderButton(<ActionButton>Default Action</ActionButton>)
    expect(screen.getByText('Default Action')).toBeDefined()

    rerender(<ActionButton intent="primary">Primary Action</ActionButton>)
    expect(screen.getByText('Primary Action')).toBeDefined()

    rerender(<ActionButton intent="danger">Danger Action</ActionButton>)
    expect(screen.getByText('Danger Action')).toBeDefined()
  })

  it('preserves stability and accessibility across disabled state transitions: false -> true -> false', () => {
    function TestConsumer() {
      const [disabled, setDisabled] = useState(false)
      const [count, setCount] = useState(0)

      return (
        <div>
          <button
            data-testid="toggle"
            onClick={() => setDisabled((d) => !d)}
          >
            Toggle
          </button>
          <ActionButton
            intent="primary"
            disabled={disabled}
            onPress={() => setCount((c) => c + 1)}
          >
            {disabled ? 'Processing...' : 'Submit'}
          </ActionButton>
          <span data-testid="count">{count}</span>
        </div>
      )
    }

    renderButton(<TestConsumer />)

    // Initial state: disabled=false
    const submitBtn = screen.getByText('Submit')
    expect(submitBtn).toBeDefined()

    // Transition to disabled=true
    fireEvent.click(screen.getByTestId('toggle'))
    expect(screen.getByText('Processing...')).toBeDefined()

    // Transition back to disabled=false
    fireEvent.click(screen.getByTestId('toggle'))
    expect(screen.getByText('Submit')).toBeDefined()
  })

  it('handles click events when enabled and ignores when disabled', () => {
    const onPress = vi.fn()
    const { rerender } = renderButton(
      <ActionButton
        disabled={false}
        onPress={onPress}
      >
        Click Me
      </ActionButton>
    )

    const btn = screen.getByText('Click Me')
    fireEvent.click(btn)
    expect(onPress).toHaveBeenCalledTimes(1)

    // Now disable
    rerender(
      <ActionButton
        disabled={true}
        onPress={onPress}
      >
        Click Me
      </ActionButton>
    )

    fireEvent.click(btn)
    // Should still have been called only once
    expect(onPress).toHaveBeenCalledTimes(1)
  })
})
