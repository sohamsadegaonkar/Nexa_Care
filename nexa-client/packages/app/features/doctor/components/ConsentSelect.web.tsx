import { useState, type CSSProperties, type ChangeEvent } from 'react'
import type { ConsentSelectProps, ConsentSelectValue } from './ConsentSelect.types'

const labelStyle: CSSProperties = {
  color: 'var(--color11)',
  fontFamily: 'inherit',
  fontSize: 15,
}

const selectStyle: CSSProperties = {
  width: '100%',
  minHeight: 46,
  padding: '0 14px',
  border: '1px solid var(--borderColor)',
  borderRadius: 9,
  background: 'var(--background)',
  color: 'var(--color12)',
  fontFamily: 'inherit',
  fontSize: 16,
}

/** Accessible DOM implementation that deliberately does not mount Tamagui Select. */
export function ConsentSelect<T extends ConsentSelectValue>({
  id,
  label,
  value,
  options,
  onValueChange,
  disabled = false,
}: ConsentSelectProps<T>) {
  const [focused, setFocused] = useState(false)

  const handleChange = (event: ChangeEvent<HTMLSelectElement>) => {
    const selected = options.find((option) => String(option.value) === event.currentTarget.value)
    if (selected) onValueChange(selected.value)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <label htmlFor={id} style={labelStyle}>{label}</label>
      <select
        id={id}
        aria-label={label}
        value={String(value)}
        disabled={disabled}
        onChange={handleChange}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        style={{
          ...selectStyle,
          cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.6 : 1,
          outline: focused ? '2px solid var(--blue8)' : 'none',
          outlineOffset: focused ? 2 : 0,
        }}
      >
        {options.map((option) => (
          <option key={String(option.value)} value={String(option.value)}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  )
}
